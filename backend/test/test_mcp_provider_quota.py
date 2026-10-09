import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.mcp import provider_quota
from app.services.mcp.client import McpClientError


class FakeAsyncRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None):
        self.values[key] = (value, ex)


class FakeSyncRedis:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error

    def mget(self, keys):
        if self.error:
            raise self.error
        return [self.values.get(key) for key in keys]


class ProviderQuotaTests(unittest.TestCase):
    def test_only_amap_daily_quota_code_counts(self):
        def error(code, message):
            return McpClientError(code, "x", safe_details={"upstream_message": message})

        self.assertTrue(provider_quota.is_daily_quota_exhausted(error("tool_error", "USER_DAILY_QUERY_OVER_LIMIT")))
        self.assertFalse(provider_quota.is_daily_quota_exhausted(error("tool_error", "CUQPS_HAS_EXCEEDED_THE_LIMIT")))
        self.assertFalse(provider_quota.is_daily_quota_exhausted(error("network_error", "USER_DAILY_QUERY_OVER_LIMIT")))
        self.assertFalse(provider_quota.is_daily_quota_exhausted(McpClientError("tool_error", "x")))

    def test_search_tools_share_one_quota_group(self):
        for name in ("maps_text_search", "maps_around_search", "maps_search_detail"):
            self.assertEqual(provider_quota.amap_quota_group(name), provider_quota.AMAP_SEARCH_QUOTA_GROUP)
        self.assertEqual(provider_quota.amap_quota_group("maps_geo"), "maps_geo")

    def test_ttl_runs_to_first_of_next_month_beijing_midnight(self):
        # 高德个人配额按月计，错误码叫 DAILY 也要等到下月 1 日才恢复。
        now = datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(provider_quota.seconds_until_quota_reset(now), 30 * 60 + 60)
        utc_now = datetime(2026, 10, 31, 15, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(provider_quota.seconds_until_quota_reset(utc_now), 30 * 60 + 60)
        mid_month = datetime(2026, 10, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(provider_quota.seconds_until_quota_reset(mid_month), 22 * 86400 + 60)
        december = datetime(2026, 12, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(provider_quota.seconds_until_quota_reset(december), (16 * 86400 + 12 * 3600) + 60)

    def test_mark_then_read_round_trip(self):
        client = FakeAsyncRedis()
        asyncio.run(provider_quota.mark_quota_exhausted("s1", "search", redis_client=client))
        ((key, (value, ttl)),) = client.values.items()
        self.assertGreater(ttl, 0)

        with patch.object(provider_quota, "_get_sync_client", return_value=FakeSyncRedis({key: value})):
            self.assertEqual(
                provider_quota.read_exhausted_quota_groups("s1", frozenset({"search", "maps_geo"})),
                frozenset({"search"}),
            )
            self.assertEqual(provider_quota.read_exhausted_quota_groups("s2", frozenset({"search"})), frozenset())

    def test_redis_failure_treats_everything_as_available(self):
        with patch.object(provider_quota, "_get_sync_client", return_value=FakeSyncRedis(error=OSError("down"))):
            self.assertEqual(provider_quota.read_exhausted_quota_groups("s1", frozenset({"search"})), frozenset())
        with patch.object(provider_quota, "_get_sync_client", return_value=None):
            self.assertEqual(provider_quota.read_exhausted_quota_groups("s1", frozenset({"search"})), frozenset())


if __name__ == "__main__":
    unittest.main()
