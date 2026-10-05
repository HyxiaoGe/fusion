"""产品工具与通用联网工具一起公告，工具失败时可以换用。"""

import unittest

from app.services.mcp.amap_product_tools import AMAP_PRODUCT_DEFINITIONS
from app.services.stream.agent_loop_request_prep import build_agent_loop_call_config


class RecoveryRouteTests(unittest.TestCase):
    def test_weather_request_schemas_include_recovery_without_requiring_it(self):
        weather = next(tool for tool in AMAP_PRODUCT_DEFINITIONS if tool["function"]["name"] == "weather_forecast")
        config = build_agent_loop_call_config(
            provider="openai",
            options={"plan_mode": "on"},
            capabilities={"functionCalling": True, "searchCapable": True},
            additional_tools=[weather],
            dynamic_tool_handlers={"weather_forecast": lambda _: None},
        )
        assert set(config.announced_tools) == {"weather_forecast", "web_search", "url_read"}
        assert {tool["function"]["name"] for tool in config.call_kwargs["tools"]} == {
            "weather_forecast",
            "web_search",
            "url_read",
            "update_plan",
        }


class RecoveryPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_policy_reaches_weather_and_verified_model_context(self):
        from app.services.stream.agent_loop_request_prep import prepare_agent_loop_messages

        async def messages(*args, **kwargs):
            return [{"role": "user", "content": "查询"}]

        for message in ("香港三天天气", "核验 OpenAI 最新公告，给出官方原文和交叉来源"):
            config = build_agent_loop_call_config(
                provider="openai",
                options={"plan_mode": "off"},
                capabilities={"functionCalling": True, "searchCapable": True},
                additional_tools=AMAP_PRODUCT_DEFINITIONS,
                dynamic_tool_handlers={tool["function"]["name"]: lambda _: None for tool in AMAP_PRODUCT_DEFINITIONS},
            )
            prepared = await prepare_agent_loop_messages(
                db=object(),
                user_id="user-1",
                raw_messages=[],
                has_vision=False,
                file_ids=None,
                original_message=message,
                call_config=config,
                file_repo_factory=lambda _: object(),
                load_user_system_prompt_fn=lambda *_: None,
                build_llm_messages_fn=messages,
                preprocess_user_input=False,
            )
            assert "tool_failure_policy" in prepared.prompt_assembly["section_ids"]
            assert any(
                "use another announced tool" in item["content"].lower()
                for item in prepared.messages
                if item["role"] == "system"
            )
