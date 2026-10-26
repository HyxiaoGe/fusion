"""后端产品埋点封装：没 key 空操作、游客不建人档、SDK 抛错不外溢。"""

import unittest
from unittest.mock import MagicMock, patch

from app.core import product_analytics
from app.core.config import settings


class ProductAnalyticsTests(unittest.TestCase):
    def setUp(self):
        product_analytics.reset_for_tests()
        self.addCleanup(product_analytics.reset_for_tests)

    def test_disabled_without_key_never_builds_client(self):
        with patch.object(settings, "POSTHOG_API_KEY", ""), patch("posthog.Posthog") as client_cls:
            product_analytics.track("user-1", "run_completed", {"run_id": "r1"})
            product_analytics.shutdown()
        client_cls.assert_not_called()

    def test_capture_uses_user_id_and_marks_guest_without_person_profile(self):
        client = MagicMock()
        with (
            patch.object(settings, "POSTHOG_API_KEY", "phc_test"),
            patch.object(settings, "POSTHOG_HOST", "https://us.i.posthog.com"),
            patch("posthog.Posthog", return_value=client) as client_cls,
        ):
            product_analytics.track("user-1", "credits_settled", {"credits": 3.5})
            product_analytics.track("guest-1", "credits_exhausted", {"tier": "guest", "is_guest": True})
            product_analytics.track(None, "credits_exhausted", {"tier": "user"})
            product_analytics.shutdown()

        client_cls.assert_called_once()
        self.assertEqual(client_cls.call_args.args, ("phc_test",))
        self.assertEqual(client_cls.call_args.kwargs["host"], "https://us.i.posthog.com")
        self.assertEqual(
            [call.kwargs for call in client.capture.call_args_list],
            [
                {"distinct_id": "user-1", "properties": {"credits": 3.5}},
                {
                    "distinct_id": "guest-1",
                    "properties": {"tier": "guest", "is_guest": True, "$process_person_profile": False},
                },
            ],
        )
        self.assertEqual(
            [call.args for call in client.capture.call_args_list], [("credits_settled",), ("credits_exhausted",)]
        )
        client.shutdown.assert_called_once()

    def test_sdk_errors_are_swallowed(self):
        client = MagicMock()
        client.capture.side_effect = RuntimeError("boom")
        client.shutdown.side_effect = RuntimeError("boom")
        with patch.object(settings, "POSTHOG_API_KEY", "phc_test"), patch("posthog.Posthog", return_value=client):
            product_analytics.track("user-1", "run_completed", {})
            product_analytics.shutdown()
        client.capture.assert_called_once()
