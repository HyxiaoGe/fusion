import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.stream.itinerary_observability import (
    ItineraryToolObservation,
    build_itinerary_run_payload,
    build_itinerary_tool_observation,
    classify_itinerary_result,
    emit_itinerary_run_log,
)


def _record(
    tool_name: str,
    *,
    status: str = "success",
    data: dict | None = None,
    duration_ms: int = 12,
):
    return SimpleNamespace(
        tool_name=tool_name,
        result=SimpleNamespace(
            status=status,
            data=data or {},
            duration_ms=duration_ms,
        ),
    )


class ItineraryObservabilityTests(unittest.TestCase):
    def test_non_itinerary_product_tools_do_not_create_run_payload(self):
        observations = [
            build_itinerary_tool_observation(_record("weather_forecast")),
            build_itinerary_tool_observation(_record("route_compare")),
        ]

        payload = build_itinerary_run_payload(
            run_id="run-1",
            model_id="model-1",
            provider="provider-1",
            content_blocks=[],
            tool_observations=[item for item in observations if item is not None],
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=1234,
            total_steps=2,
            total_tool_calls=2,
        )

        self.assertIsNone(payload)

    def test_complete_itinerary_payload_has_fixed_safe_dimensions(self):
        observations = [
            ItineraryToolObservation("search_flights", "success", None, 120, False, False, False, False),
            ItineraryToolObservation("weather_forecast", "degraded", None, 80, False, False, False, False),
        ]

        payload = build_itinerary_run_payload(
            run_id="run-1",
            model_id="model-1",
            provider="provider-1",
            content_blocks=[{"type": "itinerary_results", "status": "success"}],
            tool_observations=observations,
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=1234,
            total_steps=2,
            total_tool_calls=2,
        )

        self.assertEqual(payload["result"], "complete")
        self.assertEqual(payload["model_id"], "model-1")
        self.assertEqual(payload["provider"], "provider-1")
        self.assertEqual(payload["total_duration_ms"], 1234)
        self.assertEqual(
            payload["tool_outcomes"],
            {
                "search_flights": {"success": 1, "degraded": 0, "failed": 0, "timeout": 0},
                "weather_forecast": {"success": 0, "degraded": 1, "failed": 0, "timeout": 0},
            },
        )

    def test_failed_and_partial_results_follow_travel_query_outcomes(self):
        failed = build_itinerary_run_payload(
            run_id="run-failed",
            model_id="model-1",
            provider="provider-1",
            content_blocks=[],
            tool_observations=[
                ItineraryToolObservation("search_trains", "failed", "upstream_error", 100, True, False, False, True)
            ],
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=100,
            total_steps=1,
            total_tool_calls=1,
        )
        partial = build_itinerary_run_payload(
            run_id="run-partial",
            model_id="model-1",
            provider="provider-1",
            content_blocks=[],
            tool_observations=[
                ItineraryToolObservation("search_trains", "success", None, 100, False, False, False, False),
                ItineraryToolObservation("search_flights", "timeout", "timeout", 100, False, False, False, False),
            ],
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=100,
            total_steps=1,
            total_tool_calls=2,
        )

        self.assertEqual(failed["result"], "failed")
        self.assertEqual(failed["upstream_error_count"], 1)
        self.assertEqual(partial["result"], "partial")

    def test_ticket_query_without_itinerary_card_is_complete(self):
        # 只查票时模型不展示行程卡片，这是正常交付，不算失败。
        payload = build_itinerary_run_payload(
            run_id="run-no-card",
            model_id="model-1",
            provider="provider-1",
            content_blocks=[],
            tool_observations=[
                ItineraryToolObservation("search_flights", "success", None, 100, False, False, False, False)
            ],
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=100,
            total_steps=1,
            total_tool_calls=1,
        )

        self.assertEqual(payload["result"], "complete")

    def test_itinerary_result_classification_respects_terminal_status(self):
        succeeded = [ItineraryToolObservation("search_flights", "success", None, 10, False, False, False, False)]
        degraded = [ItineraryToolObservation("search_trains", "degraded", None, 10, True, False, False, False)]
        weather_only = [ItineraryToolObservation("weather_forecast", "success", None, 10, False, False, False, False)]

        self.assertEqual(classify_itinerary_result(succeeded, run_status="completed"), "complete")
        self.assertEqual(classify_itinerary_result(degraded, run_status="completed"), "partial")
        for terminal_status in ("limit_reached", "incomplete", "interrupted"):
            with self.subTest(terminal_status=terminal_status):
                self.assertEqual(classify_itinerary_result(succeeded, run_status=terminal_status), "partial")
        self.assertEqual(classify_itinerary_result(succeeded, run_status="error"), "failed")
        self.assertEqual(classify_itinerary_result(weather_only, run_status="completed"), "failed")
        self.assertEqual(classify_itinerary_result([], run_status="completed"), "failed")

    def test_tool_observation_normalizes_timeout_repair_and_budget_without_raw_values(self):
        timeout = build_itinerary_tool_observation(
            _record(
                "search_flights",
                status="failed",
                data={
                    "error_code": "call_timeout",
                    "private_payload": "secret-value",
                },
            )
        )
        repair = build_itinerary_tool_observation(
            _record(
                "search_trains",
                status="failed",
                data={
                    "error_code": "arguments_schema_invalid",
                    "repair": {"retryable": True, "allowed_values": ["private-city"]},
                },
            )
        )
        budget = build_itinerary_tool_observation(
            _record(
                "search_trains",
                status="failed",
                data={"error_code": "travel_run_budget_exhausted"},
            )
        )

        self.assertEqual(timeout.outcome, "timeout")
        self.assertEqual(timeout.error_category, "timeout")
        self.assertEqual(repair.outcome, "degraded")
        self.assertTrue(repair.controlled_repair)
        self.assertEqual(budget.error_category, "travel_budget_exhausted")
        self.assertTrue(budget.travel_budget_exhausted)
        self.assertFalse(budget.server_budget_exhausted)
        self.assertNotIn("secret-value", repr((timeout, repair, budget)))
        self.assertNotIn("private-city", repr((timeout, repair, budget)))

    def test_log_serialization_never_contains_unapproved_fields(self):
        payload = build_itinerary_run_payload(
            run_id="run-safe",
            model_id="model-safe",
            provider="provider-safe",
            content_blocks=[{"type": "itinerary_results", "status": "success", "origin": "private-city"}],
            tool_observations=[
                ItineraryToolObservation("search_flights", "success", None, 10, False, False, False, False)
            ],
            run_status="completed",
            finish_reason="stop",
            limit_reason=None,
            total_duration_ms=10,
            total_steps=1,
            total_tool_calls=1,
        )

        with patch("app.services.stream.itinerary_observability.logger.info") as info:
            emit_itinerary_run_log(payload)

        logged = " ".join(str(arg) for arg in info.call_args.args)
        self.assertNotIn("private-city", logged)
        parsed = json.loads(logged.split(" ", 1)[1])
        self.assertEqual(parsed, payload)
