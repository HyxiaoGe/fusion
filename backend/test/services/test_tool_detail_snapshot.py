"""工具详情只遮盖凭据，业务正文与有界截断分别记录。"""

import copy
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from app.services.tool_detail_snapshot import build_tool_detail_snapshot


class ToolDetailSnapshotTests(unittest.TestCase):
    def test_keeps_business_fields_and_copies_nested_values(self):
        payload = {
            "city": "南京",
            "date": "2026-09-07",
            "prompt": "查询天气",
            "messages": [{"content": "周末适合散步吗？"}],
            "path": "/weather/nanjing",
            "token_count": 37,
        }
        result = {"content": "多云，适合散步", "rawresponse": {"temperature": 26}, "empty": {}}
        original = copy.deepcopy((payload, result))

        snapshot = build_tool_detail_snapshot(payload, result)

        self.assertEqual(
            snapshot,
            {
                "schema_version": 1,
                "payload": payload,
                "result": result,
                "error": None,
                "redacted_fields": [],
                "truncated_fields": [],
            },
        )
        snapshot["payload"]["messages"][0]["content"] = "独立副本"
        snapshot["result"]["rawresponse"]["temperature"] = 99
        self.assertEqual((payload, result), original)

    def test_masks_only_explicit_credential_keys(self):
        credentials = {
            "APIKey": "key-value",
            "Authorization": "auth-value",
            "Cookie": "cookie-value",
            "token": "token-value",
            "password": "password-value",
            "private_key": "private-value",
            "client_secret": "client-value",
            "access_token": "access-value",
        }
        snapshot = build_tool_detail_snapshot({"nested": credentials}, {"token_usage": 50})

        self.assertEqual(set(snapshot["payload"]["nested"].values()), {"[REDACTED]"})
        self.assertEqual(snapshot["result"], {"token_usage": 50})
        self.assertEqual(snapshot["redacted_fields"], sorted(f"payload.nested.{key}" for key in credentials))
        self.assertEqual(snapshot["truncated_fields"], [])

    def test_keeps_normal_url_query_and_removes_credentials(self):
        safe_url = "https://example.com/weather?city=%E5%8D%97%E4%BA%AC&date=2026-09-07#forecast"
        url = "https://alice:password@example.com/weather?city=南京&api_key=secret&date=2026-09-07#forecast"
        snapshot = build_tool_detail_snapshot({"url": url, "safe_url": safe_url}, {})

        parsed = urlsplit(snapshot["payload"]["url"])
        self.assertEqual(parsed.netloc, "example.com")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "city": ["南京"],
                "api_key": ["[REDACTED]"],
                "date": ["2026-09-07"],
            },
        )
        self.assertEqual(parsed.fragment, "forecast")
        self.assertEqual(snapshot["payload"]["safe_url"], safe_url)
        self.assertEqual(snapshot["redacted_fields"], ["payload.url"])

    def test_masks_embedded_urls_bearer_and_common_tokens_in_body_and_error(self):
        body = "正文 https://example.com?q=天气&token=secret Bearer test-secret sk-abcdefghijklmnopqrstuvwx"
        snapshot = build_tool_detail_snapshot({}, {"content": body}, "失败：Bearer error-secret")

        self.assertIn("q=天气", snapshot["result"]["content"])
        self.assertNotIn("secret", snapshot["result"]["content"])
        self.assertNotIn("abcdefghijklmnopqrstuvwx", snapshot["result"]["content"])
        self.assertEqual(snapshot["error"], "失败：[REDACTED]")
        self.assertEqual(snapshot["redacted_fields"], ["error", "result.content"])

    def test_masks_explicit_inline_credentials_without_hiding_business_fields(self):
        body = (
            "token=plain-token; Cookie: sessionid=cookie-secret; theme=light\n"
            "Authorization: Basic basic-secret\n"
            '"access_token": "json-secret", "token_count": 37\n'
            "token_count=37; city=南京; api key: 如何申请地图服务"
        )
        snapshot = build_tool_detail_snapshot({}, {"content": body}, "失败 token=error-secret")

        for secret in ("plain-token", "cookie-secret", "basic-secret", "json-secret", "error-secret"):
            self.assertNotIn(secret, json.dumps(snapshot))
        self.assertIn("token_count=37", snapshot["result"]["content"])
        self.assertIn('"token_count": 37', snapshot["result"]["content"])
        self.assertIn("city=南京", snapshot["result"]["content"])
        self.assertIn("api key: 如何申请地图服务", snapshot["result"]["content"])
        self.assertEqual(snapshot["redacted_fields"], ["error", "result.content"])

    def test_separates_truncation_from_redaction_and_preserves_prefix(self):
        snapshot = build_tool_detail_snapshot({}, {"content": "天气详情" + "晴" * 100_000, "items": list(range(250))})

        self.assertTrue(snapshot["result"]["content"].startswith("天气详情"))
        self.assertLess(len(snapshot["result"]["content"]), 100_000)
        self.assertIn("result.content", snapshot["truncated_fields"])
        self.assertEqual(snapshot["redacted_fields"], [])

    def test_limits_list_and_depth_without_losing_surrounding_data(self):
        nested = {"value": "最深处"}
        for _ in range(15):
            nested = {"next": nested}
        snapshot = build_tool_detail_snapshot({}, {"city": "南京", "items": list(range(250)), "deep": nested})

        self.assertEqual(snapshot["result"]["city"], "南京")
        self.assertEqual(snapshot["result"]["items"], list(range(200)))
        self.assertIn("result.items", snapshot["truncated_fields"])
        self.assertTrue(any(path.startswith("result.deep") for path in snapshot["truncated_fields"]))

    def test_bounds_total_utf8_size_and_metadata_while_retaining_both_sections(self):
        payload = {f"参数{index}": "内容" * 20_000 for index in range(300)}
        result = {"city": "南京", "content": "天气" * 50_000, "more": ["晴" * 10_000] * 250}
        snapshot = build_tool_detail_snapshot(payload, result, "错误" * 10_000)

        self.assertLessEqual(len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")), 128 * 1024)
        self.assertTrue(snapshot["payload"]["参数0"].startswith("内容"))
        self.assertEqual(snapshot["result"]["city"], "南京")
        self.assertTrue(snapshot["result"]["content"].startswith("天气"))
        self.assertLessEqual(len(snapshot["truncated_fields"]), 64)

    def test_bounds_credential_metadata_and_handles_empty_inputs(self):
        result = {f"项目{index}": {"api_key": f"secret-{index}"} for index in range(200)}
        snapshot = build_tool_detail_snapshot({}, result)

        self.assertNotIn("secret-", json.dumps(snapshot, ensure_ascii=False))
        self.assertLessEqual(len(snapshot["redacted_fields"]), 64)
        self.assertTrue(all(len(path.encode("utf-8")) <= 128 for path in snapshot["redacted_fields"]))
        self.assertEqual(build_tool_detail_snapshot({}, {})["payload"], {})
        self.assertEqual(build_tool_detail_snapshot({}, {})["result"], {})

    def test_masks_credentials_across_string_limit_and_unfinished_private_key(self):
        body = "x" * (32 * 1024 - 10) + " sk-abcdefghijklmnopqrstuvwx"
        private_key = "-----BEGIN PRIVATE KEY-----\n" + "sensitive-value" * 3000
        snapshot = build_tool_detail_snapshot({}, {"content": body, "rawresponse": private_key})

        self.assertNotIn("sk-", snapshot["result"]["content"])
        self.assertNotIn("sensitive-value", snapshot["result"]["rawresponse"])
        self.assertIn("result.content", snapshot["redacted_fields"])
        self.assertIn("result.rawresponse", snapshot["redacted_fields"])
        self.assertIn("result.rawresponse", snapshot["truncated_fields"])

    def test_bounds_escaped_paths_and_keeps_ordinary_query_encoding(self):
        fields = {f"{index}" + "\n" * 100: {"api_key": "secret"} for index in range(63)}
        url = "https://example.com?city=New+York&date=2026%2D09%2D07&%61pi_key=secret#access_token=secret"
        snapshot = build_tool_detail_snapshot({"url": url}, fields)

        self.assertTrue(
            all(
                len(json.dumps(path, ensure_ascii=False).encode("utf-8")) <= 128 for path in snapshot["redacted_fields"]
            )
        )
        self.assertIn("city=New+York&date=2026%2D09%2D07", snapshot["payload"]["url"])
        self.assertNotIn("secret", json.dumps(snapshot))
        self.assertLessEqual(len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")), 128 * 1024)
