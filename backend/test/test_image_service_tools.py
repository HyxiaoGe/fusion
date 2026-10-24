import os
import unittest

import httpx

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.core.logger import log_context  # noqa: E402
from app.services.mcp.agent_tools import (  # noqa: E402
    McpAgentServerCircuitBreaker,
    McpAgentToolConcurrencyLimiter,
    load_mcp_agent_tools,
)
from app.services.mcp.image_service_tools import (  # noqa: E402
    EXECUTION_TIMEOUT_SECONDS,
    MCP_CALL_TIMEOUT_SECONDS,
    ImageGenerationError,
    build_generate_image_definition,
    closest_aspect_ratio,
    parse_generate_image_text,
)
from app.services.stream.tool_executor import AGENT_TOOL_TIMEOUT, _handler_timeout_seconds  # noqa: E402
from test.fake_tool_handler import FakeToolHandler  # noqa: E402
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository, build_row  # noqa: E402

ENDPOINT = "http://agent-tools-gateway:8100/image/mcp"
IMAGE_NAME = "4ce9c7606275acf72358b93aabf8a8e5b90ed5431254fcda3718caf60f0f8d55.jpeg"
GATEWAY_IMAGE_URL = f"http://agent-tools-gateway:8100/image/static/images/{IMAGE_NAME}"
SOURCE_URL = f"http://image-service.dev.seanfield.org/static/images/{IMAGE_NAME}"
SUCCESS_TEXT = (
    f"Generated image: http://image-service.dev.seanfield.org/static/images/{IMAGE_NAME}\n"
    "Model: gemini-3.1-flash-image-preview\nCached: False\nID: 7ca2c0c7-4477-456c-8a3c-76960859f1bf"
)


def build_image_row(**overrides):
    values = {
        "id": "image-server",
        "name": "Image Service",
        "provider": "image-service",
        "endpoint_url": ENDPOINT,
        "allowed_tools": ["generate_image"],
        "discovered_tools": [
            {
                "name": "generate_image",
                "description": "Generate an image",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "aspect_ratio": {"type": "string"},
                        "style": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            }
        ],
    }
    values.update(overrides)
    return build_row(**values)


class TimeoutAwareClientManager(FakeClientManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.min_timeouts = []
        self.request_headers = []

    async def call_tool(self, config, tool_name, arguments, *, min_call_timeout_seconds=None, request_headers=None):
        self.min_timeouts.append(min_call_timeout_seconds)
        self.request_headers.append(request_headers)
        return await super().call_tool(config, tool_name, arguments)


class FakeImageServer:
    def __init__(self, *, status=200, content_type="image/jpeg", body=b"jpeg-bytes"):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.requested_urls = []

    def factory(self):
        def handle(request):
            self.requested_urls.append(str(request.url))
            return httpx.Response(self.status, headers={"content-type": self.content_type}, content=self.body)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


class StoreRecorder:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"file_id": "file-1", "mime_type": "image/jpeg", "width": 1024, "height": 1024}


class SourceLoader:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def load_image_tools(rows, client, *, store=None, loader=None, conversation_id="conv-1", user_id="user-1"):
    repository = FakeRepository(rows)
    return load_mcp_agent_tools(
        object(),
        user_id=user_id,
        conversation_id=conversation_id,
        client_manager=client,
        session_factory=FakeDb,
        repository_factory=lambda _db: repository,
        concurrency_limiter=McpAgentToolConcurrencyLimiter(global_limit=4),
        circuit_breaker=McpAgentServerCircuitBreaker(failure_threshold=3, cooldown_seconds=30),
        store_generated_image=store or StoreRecorder(),
        load_generated_image=loader or SourceLoader(),
    )


class ImageServiceToolLoadingTests(unittest.TestCase):
    def test_image_service_row_registers_only_product_tool(self):
        tool_set = load_image_tools([build_image_row()], TimeoutAwareClientManager())

        self.assertEqual(list(tool_set.handlers), ["generate_image"])
        definition = tool_set.definitions[0]["function"]
        self.assertEqual(definition["name"], "generate_image")
        self.assertEqual(definition["parameters"]["required"], ["prompt"])
        self.assertEqual(tool_set.audit_bindings[0]["remote_tool_name"], "product:generate_image")
        self.assertNotIn("endpoint_url", tool_set.audit_bindings[0])

    def test_missing_conversation_or_unauthorized_remote_tool_registers_nothing(self):
        cases = {
            "no_conversation": dict(rows=[build_image_row()], conversation_id=None),
            "not_allowed": dict(rows=[build_image_row(allowed_tools=[])], conversation_id="conv-1"),
        }
        for name, case in cases.items():
            with self.subTest(name):
                tool_set = load_image_tools(
                    case["rows"], TimeoutAwareClientManager(), conversation_id=case["conversation_id"]
                )
                self.assertEqual(tool_set.handlers, {})


class ImageServiceToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    def build_handler(self, *, text=SUCCESS_TEXT, server=None, store=None, client=None, loader=None):
        client = client or TimeoutAwareClientManager(result={"content": [{"type": "text", "text": text}]})
        store = store or StoreRecorder()
        tool_set = load_image_tools([build_image_row()], client, store=store, loader=loader)
        handler = tool_set.handlers["generate_image"]
        server = server or FakeImageServer()
        handler.http_client_factory = server.factory
        return handler, client, store, server

    async def test_success_downloads_from_endpoint_origin_and_stores_for_bound_conversation(self):
        handler, client, store, server = self.build_handler()

        result = await handler.execute({"prompt": " a red fox ", "aspect_ratio": "16:9"})

        self.assertEqual(result.status, "success")
        self.assertEqual(client.calls[0][1], "generate_image")
        self.assertEqual(
            client.calls[0][2],
            {"prompt": "a red fox", "aspect_ratio": "16:9", "grounding": True},
        )
        self.assertEqual(client.min_timeouts, [MCP_CALL_TIMEOUT_SECONDS])
        # 产品身份由服务密钥认定；这里只带用户与场景供网关与 image-service 统计，不进模型参数。
        self.assertEqual(client.request_headers, [{"X-Caller-User": "user-1", "X-Caller-Scene": "chat_image"}])
        self.assertEqual(server.requested_urls, [GATEWAY_IMAGE_URL])
        self.assertEqual(store.calls[0]["conversation_id"], "conv-1")
        self.assertEqual(store.calls[0]["user_id"], "user-1")
        self.assertEqual(store.calls[0]["content"], b"jpeg-bytes")
        self.assertEqual(store.calls[0]["source_url"], SOURCE_URL)

        block = handler.build_content_block(result, "blk_1", "log_1")
        self.assertEqual(block.type, "generated_image")
        self.assertEqual(block.file_id, "file-1")
        self.assertEqual(block.model, "gemini-3.1-flash-image-preview")
        self.assertEqual(block.tool_call_log_id, "log_1")

        # 模型拿到图片编号用于之后编辑，拿不到任何图片地址。
        context = handler.format_llm_context(result)
        self.assertNotIn("http", context)
        self.assertNotIn(IMAGE_NAME, context)
        self.assertIn("Image id: file-1", context)
        self.assertIn("never show it to the user", context)
        self.assertIn("what was drawn", context)

    async def test_run_id_is_sent_as_request_correlation(self):
        handler, client, _store, _server = self.build_handler()

        with log_context(run_id="run-123"):
            await handler.execute({"prompt": "a red fox"})

        self.assertEqual(client.request_headers[0]["X-Request-ID"], "run-123")

    async def test_failures_are_reported_without_block_or_url(self):
        cases = {
            "upstream_generation_failed": dict(text="Error generating image: quota"),
            "invalid_response": dict(text="Generated image: http://evil.example/../etc/passwd"),
            "image_download_failed": dict(server=FakeImageServer(status=404)),
            "image_download_failed:mime": dict(server=FakeImageServer(content_type="text/html")),
            "image_store_rejected": dict(store=StoreRecorder(error=ValueError("bad"))),
            "image_store_failed": dict(store=StoreRecorder(error=RuntimeError("oss down"))),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                handler, *_ = self.build_handler(**kwargs)
                result = await handler.execute({"prompt": "a fox"})
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.data["error_code"], name.split(":")[0])
                self.assertIsNone(handler.build_content_block(result, "blk", "log"))
                self.assertNotIn("http", handler.format_llm_context(result))

    async def test_invalid_arguments_fail_before_remote_call(self):
        handler, client, *_ = self.build_handler()
        for args in (
            {},
            {"prompt": " "},
            {"prompt": "x", "aspect_ratio": "2:1"},
            {"prompt": "x", "size": "4k"},
            {"prompt": "x", "regenerate": "yes"},
            {"prompt": "x", "reference_subject": 18},
            {"prompt": "x", "reference_subject": "x" * 81},
            {"prompt": "x", "quality": "ultra"},
            {"prompt": "x", "quality": True},
            {"prompt": "x", "model": "doubao-seedream-5-0-pro"},
            {"prompt": "x", "source_image_id": 5},
            {"prompt": "x", "reference_images": [SOURCE_URL]},
            {"prompt": "x", "title": 3},
            {"prompt": "x", "title": "超" * 13},
        ):
            with self.subTest(args=args):
                self.assertTrue(handler.validate_arguments(args))
                result = await handler.execute(args)
                self.assertEqual(result.data["error_code"], "invalid_arguments")
        self.assertEqual(client.calls, [])

    def structured_client(self, structured, text="Error generating image [x]: y"):
        return TimeoutAwareClientManager(
            result={"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}
        )

    async def test_regenerate_skips_service_cache(self):
        handler, client, *_ = self.build_handler()

        await handler.execute({"prompt": "a fox", "regenerate": True})
        await handler.execute({"prompt": "a fox", "regenerate": False})

        self.assertTrue(client.calls[0][2]["no_cache"])
        self.assertNotIn("no_cache", client.calls[1][2])

    async def test_structured_success_reports_fallback_model(self):
        client = self.structured_client(
            {
                "ok": True,
                "image_url": f"http://image-service.dev.seanfield.org/static/images/{IMAGE_NAME}",
                "model": "doubao-seedream-4-5",
                "requested_model": "gemini-3.1-flash-image-preview",
                "fallback_used": True,
                "cached": False,
                "id": "gen-1",
            },
            text=SUCCESS_TEXT,
        )
        handler, _, _, server = self.build_handler(client=client)

        result = await handler.execute({"prompt": "a fox"})

        self.assertEqual(result.status, "success")
        self.assertEqual(server.requested_urls, [GATEWAY_IMAGE_URL])
        self.assertEqual(result.data["model"], "doubao-seedream-4-5")
        self.assertTrue(result.data["fallback_used"])
        self.assertFalse(result.data["grounding_used"])
        self.assertEqual(handler.build_content_block(result, "blk", "log").model, "doubao-seedream-4-5")
        context = handler.format_llm_context(result)
        self.assertIn("do not mention the model", context)
        self.assertNotIn("doubao-seedream-4-5", context)
        self.assertNotIn("gemini-3.1-flash-image-preview", context)

    async def test_grounding_is_always_requested_and_reported(self):
        client = self.structured_client(
            {
                "ok": True,
                "image_url": f"http://image-service.dev.seanfield.org/static/images/{IMAGE_NAME}",
                "model": "gemini-3.1-flash-image-preview",
                "requested_model": "gemini-3.1-flash-image-preview",
                "fallback_used": False,
                "grounding_used": True,
                "cached": False,
                "id": "gen-2",
            },
            text=SUCCESS_TEXT,
        )
        handler, _, _, _ = self.build_handler(client=client)

        result = await handler.execute({"prompt": "iPhone 18 Pro 产品宣传图"})

        self.assertEqual(result.status, "success")
        self.assertIs(client.calls[0][2]["grounding"], True)
        self.assertTrue(result.data["grounding_used"])

    async def test_quality_is_passed_only_when_given_and_recorded(self):
        handler, client, *_ = self.build_handler()

        default = await handler.execute({"prompt": "a fox"})
        high = await handler.execute({"prompt": "a fox", "quality": "high"})

        self.assertNotIn("quality", client.calls[0][2])
        self.assertEqual(client.calls[1][2]["quality"], "high")
        self.assertIsNone(default.data["quality"])
        self.assertEqual(high.data["quality"], "high")

    def test_definition_offers_quality_tiers_only(self):
        properties = build_generate_image_definition()["function"]["parameters"]["properties"]

        self.assertEqual(properties["quality"]["enum"], ["standard", "high"])
        self.assertNotIn("model", properties)
        self.assertNotIn("search the web", properties["prompt"]["description"])

    async def test_title_is_kept_on_block_but_not_sent_to_service(self):
        handler, client, *_ = self.build_handler()

        titled = await handler.execute({"prompt": "a fox", "title": " 水彩插画 "})
        untitled = await handler.execute({"prompt": "a fox", "title": " "})

        self.assertNotIn("title", client.calls[0][2])
        self.assertEqual(handler.build_content_block(titled, "blk_1", "log_1").title, "水彩插画")
        self.assertIsNone(handler.build_content_block(untitled, "blk_2", "log_2").title)
        properties = build_generate_image_definition()["function"]["parameters"]["properties"]
        self.assertEqual(properties["title"]["maxLength"], 12)

    async def test_reference_subject_is_passed_only_when_given(self):
        handler, client, *_ = self.build_handler()

        await handler.execute({"prompt": "a fox"})
        await handler.execute({"prompt": "a fox", "reference_subject": " "})

        self.assertNotIn("reference_subject", client.calls[0][2])
        self.assertNotIn("reference_subject", client.calls[1][2])

    async def test_reference_search_status_reaches_result_and_context(self):
        for status, warns in (("found", False), ("cached", False), ("none_accepted", True), ("search_failed", True)):
            with self.subTest(status=status):
                client = self.structured_client(
                    {
                        "ok": True,
                        "image_url": f"http://image-service.dev.seanfield.org/static/images/{IMAGE_NAME}",
                        "model": "gemini-3.1-flash-image-preview",
                        "reference_images_used": ["https://www.apple.com/a.jpg"] if not warns else [],
                        "reference_search": {"status": status, "candidates": 8, "accepted": 0 if warns else 1},
                        "id": "gen-3",
                    },
                    text=SUCCESS_TEXT,
                )
                handler, _, _, _ = self.build_handler(client=client)

                result = await handler.execute({"prompt": "iPhone 18 Pro 产品宣传图", "reference_subject": " iPhone 18 Pro "})

                self.assertEqual(client.calls[0][2]["reference_subject"], "iPhone 18 Pro")
                self.assertEqual(result.data["reference_subject"], "iPhone 18 Pro")
                self.assertEqual(result.data["reference_search_status"], status)
                self.assertEqual(result.data["reference_image_count"], 0 if warns else 1)
                context = handler.format_llm_context(result)
                self.assertEqual("appearance may differ" in context, warns)
                if warns:
                    self.assertIn("iPhone 18 Pro", context)

    async def test_structured_failures_map_error_codes_and_guidance(self):
        cases = {
            "content_filtered": (
                {"code": "content_filtered", "retryable": False},
                "content_filtered",
                "will not help",
            ),
            "both_filtered": (
                {"code": "content_filtered", "retryable": False,
                 "fallback_from": {"model": "gemini", "code": "content_filtered"}},
                "content_filtered",
                "fallback model also refused",
            ),
            "fallback_timeout": (
                {"code": "upstream_timeout", "retryable": True,
                 "fallback_from": {"model": "gemini", "code": "content_filtered"}},
                "upstream_timeout",
                "fallback model then failed",
            ),
            "timeout": ({"code": "upstream_timeout", "retryable": True}, "upstream_timeout", "can try again"),
            "unknown": ({"code": "weird_new_code", "retryable": True}, "upstream_generation_failed", "can try again"),
        }
        for name, (error, expected_code, guidance) in cases.items():
            with self.subTest(name):
                handler, *_ = self.build_handler(client=self.structured_client({"ok": False, "error": error}))
                result = await handler.execute({"prompt": "a fox"})
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.data["error_code"], expected_code)
                context = handler.format_llm_context(result)
                self.assertIn(guidance, context)
                if expected_code == "content_filtered":
                    self.assertNotIn("try again", context)

    async def test_run_image_limit_stops_further_generation(self):
        handler, client, *_ = self.build_handler()
        handler.max_images_per_run = 1

        first = await handler.execute({"prompt": "a fox"})
        second = await handler.execute({"prompt": "a cat"})

        self.assertEqual(first.status, "success")
        self.assertEqual(second.data["error_code"], "run_image_limit_reached")
        self.assertEqual(len(client.calls), 1)

    def test_handler_declares_longer_execution_timeout_and_no_retry(self):
        handler, *_ = self.build_handler()
        self.assertFalse(handler.supports_automatic_retry)
        self.assertEqual(_handler_timeout_seconds(handler), EXECUTION_TIMEOUT_SECONDS)
        self.assertEqual(_handler_timeout_seconds(FakeToolHandler()), AGENT_TOOL_TIMEOUT)


    async def test_edit_sends_stored_source_url_and_keeps_original_shape(self):
        loader = SourceLoader({"source_url": SOURCE_URL, "width": 768, "height": 1376})
        handler, client, store, _ = self.build_handler(loader=loader)

        result = await handler.execute({"prompt": "把背景换成夜景，其余保持不变", "source_image_id": " file-0 "})

        self.assertEqual(result.status, "success")
        self.assertEqual(loader.calls, [{"file_id": "file-0", "user_id": "user-1", "conversation_id": "conv-1"}])
        self.assertEqual(client.calls[0][2]["reference_images"], [SOURCE_URL])
        self.assertEqual(client.calls[0][2]["aspect_ratio"], "9:16")
        self.assertNotIn("source_image_id", client.calls[0][2])
        self.assertEqual(client.request_headers[0]["X-Caller-Scene"], "image_edit")
        self.assertEqual(result.data["source_image_id"], "file-0")
        self.assertEqual(result.data["aspect_ratio"], "9:16")
        self.assertEqual(store.calls[0]["source_url"], SOURCE_URL)
        context = handler.format_llm_context(result)
        self.assertIn("what was changed", context)
        self.assertIn("Image id: file-1", context)

    async def test_edit_respects_explicit_aspect_ratio(self):
        loader = SourceLoader({"source_url": SOURCE_URL, "width": 768, "height": 1376})
        handler, client, *_ = self.build_handler(loader=loader)

        await handler.execute({"prompt": "裁成横版", "source_image_id": "file-0", "aspect_ratio": "16:9"})

        self.assertEqual(client.calls[0][2]["aspect_ratio"], "16:9")

    async def test_unavailable_source_fails_before_remote_call_without_spending_an_image(self):
        cases = {
            "source_image_unavailable": SourceLoader(None),
            "source_image_lookup_failed": SourceLoader(error=RuntimeError("db down")),
        }
        for code, loader in cases.items():
            with self.subTest(code):
                handler, client, *_ = self.build_handler(loader=loader)
                handler.max_images_per_run = 1

                failed = await handler.execute({"prompt": "改成蓝色", "source_image_id": "file-9"})
                new_image = await handler.execute({"prompt": "a fox"})

                self.assertEqual(failed.status, "failed")
                self.assertEqual(failed.data["error_code"], code)
                self.assertEqual(new_image.status, "success")
                self.assertEqual(len(client.calls), 1)
                self.assertNotIn("reference_images", client.calls[0][2])
        unavailable, *_ = self.build_handler(loader=SourceLoader(None))
        failed = await unavailable.execute({"prompt": "改成蓝色", "source_image_id": "file-9"})
        context = unavailable.format_llm_context(failed)
        self.assertIn("cannot be edited directly", context)
        self.assertIn("Do not call again with this id or a guessed one", context)
        # dev 实测：模型改为按描述重画后对用户说「其他保持原样」；重画时必须说明是新画的一张。
        self.assertIn("this is a new image redrawn from the description", context)
        self.assertIn("Never say that the original was changed", context)
        self.assertIn("call generate_image again without source_image_id", context)

    async def test_made_up_source_id_is_reported_as_not_editable(self):
        # dev 实测：旧会话里没有图片编号，模型编了一个带下划线的编号。按参数错误处理时模型会
        # 去掉编号重画，还对用户说「其他保持不变」；应当告诉它这张图不能直接编辑。
        for source_image_id in ("img_55d0d6a946b5e", "../file-1", "x" * 65):
            with self.subTest(source_image_id=source_image_id):
                loader = SourceLoader({"source_url": SOURCE_URL, "width": 1024, "height": 1024})
                handler, client, *_ = self.build_handler(loader=loader)
                args = {"prompt": "把背景改成白天", "source_image_id": source_image_id}

                self.assertEqual(handler.validate_arguments(args), [])
                result = await handler.execute(args)

                self.assertEqual(result.data["error_code"], "source_image_unavailable")
                self.assertEqual(loader.calls, [])
                self.assertEqual(client.calls, [])
                self.assertIn("cannot be edited directly", handler.format_llm_context(result))

    async def test_image_without_usable_service_url_is_not_offered_for_editing(self):
        client = self.structured_client(
            {
                "ok": True,
                "image_url": f"/static/images/{IMAGE_NAME}",
                "model": "doubao-seedream-5-0-flash",
                "id": "gen-4",
            },
            text=SUCCESS_TEXT,
        )
        handler, _, store, _ = self.build_handler(client=client)

        result = await handler.execute({"prompt": "a fox"})

        self.assertEqual(result.status, "success")
        self.assertIsNone(store.calls[0]["source_url"])
        self.assertFalse(result.data["editable"])
        self.assertNotIn("Image id", handler.format_llm_context(result))

    def test_definition_offers_source_image_id(self):
        properties = build_generate_image_definition()["function"]["parameters"]["properties"]

        self.assertEqual(properties["source_image_id"]["type"], "string")
        self.assertIn("generated earlier in this conversation", properties["source_image_id"]["description"])
        self.assertNotIn("reference_images", properties)

    def test_closest_aspect_ratio_follows_source_shape(self):
        cases = {
            (1024, 1024): "1:1",
            (768, 1376): "9:16",
            (1376, 768): "16:9",
            (1200, 1000): "5:4",
            (2560, 1080): "21:9",
            (0, 100): "1:1",
            (None, 100): "1:1",
        }
        for (width, height), expected in cases.items():
            with self.subTest(width=width, height=height):
                self.assertEqual(closest_aspect_ratio(width, height), expected)


class ParseGenerateImageTextTests(unittest.TestCase):
    def test_only_static_image_paths_are_accepted(self):
        self.assertEqual(parse_generate_image_text(SUCCESS_TEXT)[0], f"/static/images/{IMAGE_NAME}")
        for text in (
            "Generated image: http://host/static/images/../secret.jpeg",
            "Generated image: http://host/static/other/a.jpeg",
            "Generated image: http://host/static/images/a.svg",
            "no image here",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ImageGenerationError):
                    parse_generate_image_text(text)


if __name__ == "__main__":
    unittest.main()
