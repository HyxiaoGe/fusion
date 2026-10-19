from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.trajectory import TrajectoryCapabilityResolution
from app.services.stream.run_capability_router import (
    RunCapabilityResolution,
    resolve_run_capability_route,
    serialize_capability_resolution,
)

ALL_TOOLS = [
    "mcp_docs_query",
    "search_trains",
    "web_search",
    "route_compare",
    "url_read",
    "weather_forecast",
    "local_place_search",
    "search_flights",
]
CAPABILITIES = {"functionCalling": True, "searchCapable": True}


def _resolve(
    *,
    available_tool_names: list[str] | None = None,
    requested_plan_mode: str = "auto",
    capabilities: dict | None = None,
    tools_disabled: bool = False,
) -> RunCapabilityResolution:
    return resolve_run_capability_route(
        available_tool_names=ALL_TOOLS if available_tool_names is None else available_tool_names,
        requested_plan_mode=requested_plan_mode,
        capabilities=CAPABILITIES if capabilities is None else capabilities,
        tools_disabled=tools_disabled,
    )


def _trajectory(resolution: RunCapabilityResolution) -> TrajectoryCapabilityResolution:
    return TrajectoryCapabilityResolution.model_validate(
        {**serialize_capability_resolution(resolution), "bundle_fingerprint": "sha256:" + "0" * 64}
    )


def test_normal_run_announces_every_available_tool_in_canonical_order():
    resolution = _resolve()

    assert resolution.package_id == "agent"
    assert resolution.reason_codes == ("all_available_tools",)
    assert resolution.external_tool_names == (
        "web_search",
        "url_read",
        "weather_forecast",
        "local_place_search",
        "route_compare",
        "search_flights",
        "search_trains",
        "mcp_docs_query",
    )
    assert resolution.effective_plan_mode == "off"
    assert resolution.network_boundary_required is False
    _trajectory(resolution)


def test_plan_mode_only_turns_on_when_user_asks():
    assert _resolve(requested_plan_mode="on").effective_plan_mode == "on"
    assert _resolve(requested_plan_mode="auto").effective_plan_mode == "off"


def test_control_tools_are_never_announced_as_external():
    resolution = _resolve(available_tool_names=["web_search", "update_plan"])

    assert resolution.external_tool_names == ("web_search",)


def test_mcp_tools_are_announced_directly():
    resolution = _resolve(available_tool_names=["web_search", "mcp_docs_query"])

    assert resolution.external_tool_names == ("web_search", "mcp_docs_query")
    assert resolution.network_boundary_required is False
    assert _trajectory(resolution).deferred_tool_names == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"tools_disabled": True}, "tools_disabled"),
        ({"capabilities": {"functionCalling": False}}, "function_calling_unavailable"),
        ({"available_tool_names": []}, "required_tools_unavailable"),
    ],
)
def test_runtime_boundaries_leave_no_tools(kwargs, reason):
    resolution = _resolve(**kwargs)

    assert resolution.package_id == "tools_unavailable"
    assert resolution.reason_codes == (reason,)
    assert resolution.external_tool_names == ()
    assert resolution.effective_plan_mode == "off"
    assert resolution.network_boundary_required is True
    _trajectory(resolution)


def test_serialization_carries_no_classifier_fields():
    payload = serialize_capability_resolution(_resolve())

    assert set(payload) == {
        "schema_version",
        "router_version",
        "package_id",
        "reason_codes",
        "external_tool_names",
        "effective_plan_mode",
        "network_boundary_required",
    }
    assert payload["schema_version"] == 3


def test_legacy_package_records_are_rejected_instead_of_misread():
    with pytest.raises(ValidationError):
        TrajectoryCapabilityResolution.model_validate(
            {
                "schema_version": 2,
                "router_version": "2026-10-04.1",
                "package_id": "weather",
                "confidence": "high",
                "resolution_mode": "routed",
                "reason_codes": ["explicit_weather_request"],
                "external_tool_names": ["weather_forecast"],
                "effective_plan_mode": "off",
                "include_current_date": True,
                "network_boundary_required": False,
                "bundle_fingerprint": "sha256:" + "0" * 64,
            }
        )


def test_dto_rejects_tools_in_toolless_modes():
    with pytest.raises(ValidationError):
        TrajectoryCapabilityResolution.model_validate(
            {
                "schema_version": 3,
                "router_version": "2026-10-05.1",
                "package_id": "tools_unavailable",
                "reason_codes": ["tools_disabled"],
                "external_tool_names": ["web_search"],
                "effective_plan_mode": "off",
                "network_boundary_required": False,
                "bundle_fingerprint": "sha256:" + "0" * 64,
            }
        )
