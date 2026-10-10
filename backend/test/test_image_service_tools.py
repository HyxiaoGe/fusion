import os
import unittest

import httpx

os.environ["DATABASE_URL"] = "sqlite:///./fusion-test.db"

from app.services.mcp.agent_tools import (  # noqa: E402
    McpAgentServerCircuitBreaker,
    McpAgentToolConcurrencyLimiter,
    load_mcp_agent_tools,
)
from app.services.mcp.image_service_tools import (  # noqa: E402
    EXECUTION_TIMEOUT_SECONDS,
    FALLBACK_MODEL,
    MCP_CALL_TIMEOUT_SECONDS,
    ImageGenerationError,
    parse_generate_image_text,
)
from app.services.stream.tool_executor import AGENT_TOOL_TIMEOUT, _handler_timeout_seconds  # noqa: E402
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository, build_row  # noqa: E402

ENDPOINT = "http://192.168.1.11:8090/mcp"
IMAGE_NAME = "4ce9c7606275acf72358b93aabf8a8e5b90ed5431254fcda3718caf60f0f8d55.jpeg"
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

    async def call_tool(self, config, tool_name, arguments, *, min_call_timeout_seconds=None):
        self.min_timeouts.append(min_call_timeout_seconds)
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


def load_image_tools(rows, client, *, store=None, conversation_id="conv-1", user_id="user-1"):
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
    def build_handler(self, *, text=SUCCESS_TEXT, server=None, store=None, client=None):
        client = client or TimeoutAwareClientManager(result={"content": [{"type": "text", "text": text}]})
        store = store or StoreRecorder()
        tool_set = load_image_tools([build_image_row()], client, store=store)
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
            {"prompt": "a red fox", "aspect_ratio": "16:9", "fallback_model": FALLBACK_MODEL},
        )
        self.assertEqual(client.min_timeouts, [MCP_CALL_TIMEOUT_SECONDS])
        self.assertEqual(server.requested_urls, [f"http://192.168.1.11:8090/static/images/{IMAGE_NAME}"])
        self.assertEqual(store.calls[0]["conversation_id"], "conv-1")
        self.assertEqual(store.calls[0]["user_id"], "user-1")
        self.assertEqual(store.calls[0]["content"], b"jpeg-bytes")

        block = handler.build_content_block(result, "blk_1", "log_1")
        self.assertEqual(block.type, "generated_image")
        self.assertEqual(block.file_id, "file-1")
        self.assertEqual(block.model, "gemini-3.1-flash-image-preview")
        self.assertEqual(block.tool_call_log_id, "log_1")

        context = handler.format_llm_context(result)
        self.assertNotIn("http", context)
        self.assertNotIn("file-1", context)
        self.assertNotIn(IMAGE_NAME, context)

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
        self.assertEqual(server.requested_urls, [f"http://192.168.1.11:8090/static/images/{IMAGE_NAME}"])
        self.assertEqual(result.data["model"], "doubao-seedream-4-5")
        self.assertTrue(result.data["fallback_used"])
        self.assertEqual(handler.build_content_block(result, "blk", "log").model, "doubao-seedream-4-5")
        context = handler.format_llm_context(result)
        self.assertIn("fallback model", context)
        self.assertIn("gemini-3.1-flash-image-preview", context)

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
        self.assertEqual(_handler_timeout_seconds(object()), AGENT_TOOL_TIMEOUT)


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
