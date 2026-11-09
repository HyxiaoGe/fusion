import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.schemas.chat import GeneratedImageBlock
from app.schemas.content_block_registry import deserialize_content_blocks
from app.services.external.agent_tools_mcp import GatewayToolCatalog, GatewayToolOutcome, GatewayToolSpec
from app.services.mcp.agent_tools import load_mcp_agent_tools
from app.services.mcp.image_tools import (
    EXECUTION_TIMEOUT_SECONDS,
    GENERATE_IMAGE_TOOL_NAME,
    MAX_IMAGES_PER_RUN,
    ImageRunControls,
    ImageToolHandler,
    build_generate_image_definition,
    build_image_binding,
)
from app.services.stream.tool_executor import AGENT_TOOL_TIMEOUT, _handler_timeout_seconds
from test.fake_tool_handler import FakeToolHandler
from test.test_mcp_agent_tools import FakeClientManager, FakeDb, FakeRepository, build_row

IMAGE_ID = "7ca2c0c7-4477-456c-8a3c-76960859f1bf"
SOURCE_ID = "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"
_NULLABLE_TEXT = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}
# 与 dev 上 /image/v2/mcp 的 tools/list 同形（说明取开头一句）。
IMAGE_TOOL = {
    "name": "generate_image",
    "description": "Generate a new image from a text description, or edit an image generated earlier.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 1000},
            "aspect_ratio": {
                "anyOf": [
                    {"type": "string", "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "1:4", "4:1"]},
                    {"type": "null"},
                ],
                "default": None,
            },
            "style": _NULLABLE_TEXT,
            "regenerate": {"type": "boolean", "default": False},
            "reference_subject": _NULLABLE_TEXT,
            "quality": {"anyOf": [{"type": "string", "enum": ["standard", "high"]}, {"type": "null"}], "default": None},
            "source_image_id": _NULLABLE_TEXT,
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}
IMAGE_SPEC = GatewayToolSpec(service="image", **IMAGE_TOOL)
DEFINITION = build_generate_image_definition(IMAGE_SPEC)
SERVICE_TEXT = f"Generated the image. Image id: {IMAGE_ID}. Pass it as source_image_id to edit this image later."


def success_outcome(**image_overrides):
    image = {
        "id": IMAGE_ID,
        "mime_type": "image/png",
        "width": 1024,
        "height": 1024,
        "bytes": 123_456,
        "model": "gemini-3.1-flash-image-preview",
        "edited_from": None,
        "created_at": "2026-11-09T10:00:00Z",
    }
    image.update(image_overrides)
    return GatewayToolOutcome(
        ok=True,
        data={
            "ok": True,
            "image": image,
            "requested_model": None,
            "fallback_used": False,
            "grounding_used": False,
            "cached": False,
            "reference_search": {"status": "not_requested"},
        },
        model_text=SERVICE_TEXT,
    )


def failure_outcome(code, text="The service explains what went wrong."):
    return GatewayToolOutcome(
        ok=False,
        data={"ok": False, "error": {"code": code, "message": "m", "retryable": False}},
        model_text=text,
        error_code=code,
    )


def build_handler(*, outcome=None, call_tool=None, owned=True, controls=None, register=None):
    binding = build_image_binding(DEFINITION, conversation_id="conv-1", user_id="user-1")
    return ImageToolHandler(
        binding=binding,
        parameters=DEFINITION["function"]["parameters"],
        controls=controls or ImageRunControls(),
        call_tool=call_tool or AsyncMock(return_value=outcome or success_outcome()),
        conversation_has_image=AsyncMock(return_value=owned) if not callable(owned) else owned,
        register_image=register or AsyncMock(),
    )


def loaded_catalog(tools):
    async def list_tools(service):
        return tools if service == "image" else []

    catalog = GatewayToolCatalog(services=("image",), list_tools=list_tools)
    asyncio.run(catalog.refresh())
    return catalog


class ImageDefinitionTests(unittest.TestCase):
    def test_service_text_comes_first_and_fusion_notes_follow(self):
        function = DEFINITION["function"]

        self.assertEqual(function["name"], GENERATE_IMAGE_TOOL_NAME)
        self.assertTrue(function["description"].startswith(IMAGE_TOOL["description"] + "\n\nIn this application:"))
        self.assertIn("image card", function["description"])
        self.assertIn("source_image_id", function["parameters"]["properties"])
        self.assertNotIn("title", function["parameters"]["properties"])


class ImageRegistrationTests(unittest.TestCase):
    def load(self, catalog, *, rows=(), user_id="user-1", conversation_id="conv-1"):
        return load_mcp_agent_tools(
            object(),
            user_id=user_id,
            conversation_id=conversation_id,
            client_manager=FakeClientManager(),
            session_factory=FakeDb,
            repository_factory=lambda _db: FakeRepository(list(rows)),
            gateway_catalog=catalog,
            maps_hidden=lambda: True,
        )

    def test_generate_image_comes_from_the_catalog_and_is_announced_first(self):
        names = [f"tool_{index}" for index in range(3)]
        row = build_row(
            id="a-docs",
            allowed_tools=names,
            discovered_tools=[
                {"name": name, "description": name, "input_schema": {"type": "object"}} for name in names
            ],
        )

        tools = self.load(loaded_catalog([IMAGE_TOOL]), rows=[row])

        self.assertEqual(tools.definitions[0]["function"]["name"], GENERATE_IMAGE_TOOL_NAME)
        self.assertIsInstance(tools.handlers[GENERATE_IMAGE_TOOL_NAME], ImageToolHandler)
        self.assertEqual(tools.audit_bindings[0]["provider"], "image")
        self.assertNotIn("server_id", tools.audit_bindings[0])

    def test_not_announced_without_catalog_entry_or_conversation(self):
        self.assertEqual(self.load(loaded_catalog([])).definitions, [])
        self.assertEqual(self.load(loaded_catalog([IMAGE_TOOL]), conversation_id=None).definitions, [])
        self.assertEqual(self.load(loaded_catalog([IMAGE_TOOL]), user_id=None).definitions, [])


class ImageExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_registers_image_and_builds_v2_card(self):
        register = AsyncMock()
        call_tool = AsyncMock(return_value=success_outcome())
        handler = build_handler(call_tool=call_tool, register=register)

        result = await handler.execute({"prompt": "  一只橘猫在窗台上晒太阳  ", "aspect_ratio": "16:9"})

        self.assertEqual(result.status, "success")
        call_tool.assert_awaited_once()
        service, tool, arguments = call_tool.await_args.args
        self.assertEqual((service, tool), ("image", "generate_image"))
        self.assertEqual(arguments, {"prompt": "  一只橘猫在窗台上晒太阳  ", "aspect_ratio": "16:9"})
        self.assertEqual(call_tool.await_args.kwargs["scene"], "chat_image")
        self.assertGreaterEqual(call_tool.await_args.kwargs["min_call_timeout_seconds"], 135)
        register.assert_awaited_once_with(
            image_id=IMAGE_ID, conversation_id="conv-1", user_id="user-1", mime_type="image/png"
        )
        block = handler.build_content_block(result, "blk-1", "log-1")
        self.assertIsInstance(block, GeneratedImageBlock)
        dumped = block.model_dump(mode="json")
        self.assertEqual(dumped["schema_version"], 2)
        self.assertEqual(dumped["image_id"], IMAGE_ID)
        self.assertEqual(dumped["prompt"], "一只橘猫在窗台上晒太阳")
        self.assertEqual(dumped["aspect_ratio"], "16:9")
        self.assertEqual(dumped["model"], "gemini-3.1-flash-image-preview")
        self.assertEqual(deserialize_content_blocks([dumped])[0].type, "generated_image")
        self.assertEqual(
            handler.format_llm_context(result),
            f"{SERVICE_TEXT} {render_runtime_prompt('image.shown')}",
        )
        self.assertEqual(handler._build_result_summary(result)["image_id"], IMAGE_ID)

    async def test_edit_requires_an_image_of_this_conversation(self):
        owned = AsyncMock(return_value=True)
        call_tool = AsyncMock(return_value=success_outcome(edited_from=SOURCE_ID))
        handler = build_handler(call_tool=call_tool, owned=owned)

        result = await handler.execute({"prompt": "把背景换成夜景", "source_image_id": f" {SOURCE_ID} "})

        self.assertEqual(result.status, "success")
        owned.assert_awaited_once_with(conversation_id="conv-1", image_id=SOURCE_ID)
        self.assertEqual(call_tool.await_args.args[2]["source_image_id"], SOURCE_ID)
        self.assertEqual(call_tool.await_args.kwargs["scene"], "image_edit")
        self.assertEqual(result.data["source_image_id"], SOURCE_ID)

    async def test_unknown_source_is_not_sent_and_not_counted(self):
        call_tool = AsyncMock()
        controls = ImageRunControls(max_images=1)
        handler = build_handler(call_tool=call_tool, owned=AsyncMock(return_value=False), controls=controls)

        result = await handler.execute({"prompt": "改一下", "source_image_id": "file-123"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "source_image_unavailable")
        call_tool.assert_not_awaited()
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("image.source_image_unavailable"))
        self.assertTrue(await controls.try_reserve())

    async def test_run_limit_counts_attempts_and_refunds_not_attempted(self):
        controls = ImageRunControls()
        failures = [failure_outcome("invalid_params"), failure_outcome("upstream_timeout")]
        call_tool = AsyncMock(side_effect=[*failures, success_outcome(), success_outcome()])
        handler = build_handler(call_tool=call_tool, controls=controls)

        results = [await handler.execute({"prompt": f"图 {index}"}) for index in range(5)]

        # invalid_params 不算；upstream_timeout 可能已花钱算一次；两次成功；第 5 次到上限，不再调用。
        self.assertEqual([result.status for result in results], ["failed", "failed", "success", "success", "failed"])
        self.assertEqual(call_tool.await_count, 4)
        self.assertEqual(results[4].data["error_code"], "run_image_limit_reached")
        self.assertEqual(
            handler.format_llm_context(results[4]),
            render_runtime_prompt("image.run_image_limit_reached", max_images=MAX_IMAGES_PER_RUN),
        )
        self.assertIn(str(MAX_IMAGES_PER_RUN), handler.format_llm_context(results[4]))

    async def test_service_failure_text_goes_to_model_and_gateway_quota_is_refunded(self):
        controls = ImageRunControls(max_images=1)
        handler = build_handler(outcome=failure_outcome("content_blocked", "Blocked by policy."), controls=controls)

        result = await handler.execute({"prompt": "x"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(handler.format_llm_context(result), "Blocked by policy.")
        self.assertEqual(handler.build_content_block(result, "blk", "log"), None)
        self.assertFalse(await controls.try_reserve())

        controls = ImageRunControls(max_images=1)
        handler = build_handler(outcome=GatewayToolOutcome(ok=False, error_code="rate_limited"), controls=controls)
        result = await handler.execute({"prompt": "x"})
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("image.quota_exhausted"))
        self.assertTrue(await controls.try_reserve())

    async def test_gateway_error_without_service_text_uses_fusion_text(self):
        handler = build_handler(outcome=GatewayToolOutcome(ok=False, error_code="gateway_error"))

        result = await handler.execute({"prompt": "x"})

        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("image.unavailable"))
        self.assertEqual(handler.sanitize_output_data_for_log(result)["error_code"], "gateway_error")

    async def test_malformed_result_fails_and_deletes_the_stored_image(self):
        handler = build_handler(outcome=success_outcome(mime_type="image/gif"))

        with patch("app.services.generated_images.schedule_image_deletion") as schedule:
            result = await handler.execute({"prompt": "x"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "invalid_response")
        schedule.assert_called_once_with({IMAGE_ID})
        self.assertEqual(handler.format_llm_context(result), render_runtime_prompt("image.failed"))

    async def test_registration_failure_reports_failure_and_deletes_the_image(self):
        handler = build_handler(register=AsyncMock(side_effect=RuntimeError("db down")))

        with patch("app.services.generated_images.schedule_image_deletion") as schedule:
            result = await handler.execute({"prompt": "x"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "image_register_failed")
        schedule.assert_called_once_with({IMAGE_ID})

    async def test_log_output_has_no_service_text(self):
        handler = build_handler()

        result = await handler.execute({"prompt": "x"})
        logged = handler.sanitize_output_data_for_log(result)

        self.assertEqual(logged["image_id"], IMAGE_ID)
        self.assertNotIn("model_text", logged)
        self.assertEqual(handler.trajectory_output_data(result), logged)

    def test_no_automatic_retry_and_long_timeout(self):
        handler = build_handler()

        self.assertFalse(handler.supports_automatic_retry)
        self.assertEqual(_handler_timeout_seconds(handler), EXECUTION_TIMEOUT_SECONDS)
        self.assertEqual(_handler_timeout_seconds(FakeToolHandler()), AGENT_TOOL_TIMEOUT)

    def test_arguments_are_validated_against_the_announced_schema(self):
        handler = build_handler()

        self.assertEqual(handler.validate_arguments({"prompt": "猫"}), [])
        self.assertTrue(handler.validate_arguments({"prompt": "猫", "title": "猫"}))
        self.assertTrue(handler.validate_arguments({}))


if __name__ == "__main__":
    unittest.main()
