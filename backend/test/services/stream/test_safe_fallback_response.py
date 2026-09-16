"""安全兜底仅让辅助模型选择语言，最终正文必须来自本地模板。"""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.prompts.runtime_prompt_store import get_runtime_prompt_source
from app.services.stream.safe_fallback_response import (
    SUPPORTED_FALLBACK_LOCALES,
    FallbackResponseContext,
    default_safe_fallback,
    render_safe_fallback,
)
from app.services.utility_model import UTILITY_MAX_TOKENS


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class SafeFallbackResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolve_patch = patch(
            "app.services.stream.safe_fallback_response.resolve_utility_model",
            return_value=("openai/test-utility", "openai", {}),
        )
        self.resolve_model = self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)

    async def test_显式语言只读模板无需模型(self):
        with patch("app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock()) as model:
            for locale in SUPPORTED_FALLBACK_LOCALES:
                context = FallbackResponseContext("查询明天天气", preferred_locale=locale)
                answer = await render_safe_fallback("no_evidence", context=context, model_id="unused", timeout_s=1)
                self.assertEqual(answer, get_runtime_prompt_source(f"safe_fallback.responses.{locale}.no_evidence"))
            model.assert_not_awaited()
            self.resolve_model.assert_not_called()

    async def test_中文请求指定日语仅将原始请求用于语言选择(self):
        original = "请用日语回答：明天东京天气如何？"
        context = FallbackResponseContext(original)
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion",
            new=AsyncMock(return_value=_response('{"locale":"ja"}')),
        ) as model:
            answer = await render_safe_fallback("tool_failure", context=context, model_id="chat-model", timeout_s=2)
        kwargs = model.await_args.kwargs
        self.assertEqual(kwargs["messages"][-1], {"role": "user", "content": original})
        self.assertEqual(kwargs["max_tokens"], UTILITY_MAX_TOKENS)
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["num_retries"], 0)
        self.assertEqual(kwargs["extra_body"]["metadata"]["tags"], ["app:fusion", "phase:fallback_language"])
        self.assertEqual(answer, get_runtime_prompt_source("safe_fallback.responses.ja.tool_failure"))
        self.assertNotIn(original, answer)
        # 此测试证明输入契约和严格模板选择，不把 mock 返回值当作实际识别准确率。

    async def test_同一上下文并发或更换原因只选择一次语言(self):
        context = FallbackResponseContext("Please check tomorrow's forecast.")
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion",
            new=AsyncMock(return_value=_response('{"locale":"en"}')),
        ) as model:
            answers = await asyncio.gather(
                *(
                    render_safe_fallback(reason, context=context, model_id="chat", timeout_s=1)
                    for reason in ("tool_failure", "no_evidence", "protocol_error")
                )
            )
        model.assert_awaited_once()
        self.assertEqual(answers[2], get_runtime_prompt_source("safe_fallback.responses.en.protocol_error"))

    async def test_非法或未知输出拒绝且失败结果缓存(self):
        for raw in (
            '{"locale":"xx"}',
            '{"locale":"ja","answer":"伪造答案"}',
            '```json\n{"locale":"en"}\n```',
            '["en"]',
            "null",
            '{"locale":2}',
            "非 JSON",
        ):
            with self.subTest(raw=raw):
                context = FallbackResponseContext("Check this.")
                with patch(
                    "app.services.stream.safe_fallback_response.litellm.acompletion",
                    new=AsyncMock(return_value=_response(raw)),
                ) as model:
                    for _ in range(2):
                        answer = await render_safe_fallback(
                            "protocol_error", context=context, model_id="chat", timeout_s=1
                        )
                        self.assertEqual(answer, default_safe_fallback("protocol_error"))
                model.assert_awaited_once()

    async def test_无上下文空请求或无预算不调用模型(self):
        cases = (
            (None, 1),
            (FallbackResponseContext("  "), 1),
            (FallbackResponseContext("English"), 0),
            (FallbackResponseContext("English"), -1),
        )
        with patch("app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock()) as model:
            for context, budget in cases:
                answer = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=budget)
                self.assertEqual(answer, default_safe_fallback("no_evidence"))
        model.assert_not_awaited()

    async def test_已知语言不需要剩余预算或非空请求(self):
        with patch("app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock()) as model:
            for original in ("", "Please check."):
                answer = await render_safe_fallback(
                    "no_evidence",
                    context=FallbackResponseContext(original, "en"),
                    model_id="chat",
                    timeout_s=0,
                )
                self.assertEqual(answer, get_runtime_prompt_source("safe_fallback.responses.en.no_evidence"))
            model.assert_not_awaited()
        context = FallbackResponseContext("日本語で答えてください。")
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion",
            new=AsyncMock(return_value=_response('{"locale":"ja"}')),
        ) as model:
            await render_safe_fallback("tool_failure", context=context, model_id="chat", timeout_s=1)
            answer = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=0)
            self.assertEqual(answer, get_runtime_prompt_source("safe_fallback.responses.ja.no_evidence"))
            model.assert_awaited_once()

    async def test_非字符串偏好视为缺失而不崩溃(self):
        for preferred in ([], {"locale": "en"}, 3, False):
            with self.subTest(preferred=preferred):
                with patch(
                    "app.services.stream.safe_fallback_response.litellm.acompletion",
                    new=AsyncMock(return_value=_response('{"locale":"en"}')),
                ) as model:
                    answer = await render_safe_fallback(
                        "no_evidence",
                        context=FallbackResponseContext("Please check.", preferred),
                        model_id="chat",
                        timeout_s=1,
                    )
                    self.assertEqual(answer, get_runtime_prompt_source("safe_fallback.responses.en.no_evidence"))
                    model.assert_awaited_once()

    async def test_应用层超时取消模型并缓存失败(self):
        cancelled = asyncio.Event()

        async def slow(**_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        context = FallbackResponseContext("Check the weather.")
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock(side_effect=slow)
        ) as model:
            answer = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=0.01)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(answer, default_safe_fallback("no_evidence"))
            await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=1)
            model.assert_awaited_once()

    async def test_语言请求预算不超过辅助模型上限(self):
        async def slow(**_kwargs):
            await asyncio.Event().wait()

        with (
            patch("app.services.stream.safe_fallback_response.UTILITY_LLM_TIMEOUT", 0.01),
            patch(
                "app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock(side_effect=slow)
            ) as model,
        ):
            answer = await render_safe_fallback(
                "no_evidence",
                context=FallbackResponseContext("Check this."),
                model_id="chat",
                timeout_s=60,
            )
        self.assertLessEqual(model.await_args.kwargs["timeout"], 0.01)
        self.assertEqual(answer, default_safe_fallback("no_evidence"))

    async def test_同步目录解析不能阻塞截止时间或在超时后启动模型(self):
        release, finished = threading.Event(), threading.Event()

        def blocking_resolver(_model_id):
            release.wait(timeout=0.25)
            finished.set()
            return "openai/test", "openai", {}

        with (
            patch("app.services.stream.safe_fallback_response.resolve_utility_model", side_effect=blocking_resolver),
            patch(
                "app.services.stream.safe_fallback_response.litellm.acompletion",
                new=AsyncMock(return_value=_response('{"locale":"en"}')),
            ) as model,
        ):
            started = asyncio.get_running_loop().time()
            try:
                answer = await render_safe_fallback(
                    "no_evidence",
                    context=FallbackResponseContext("Check this."),
                    model_id="chat",
                    timeout_s=0.01,
                )
                self.assertLess(asyncio.get_running_loop().time() - started, 0.15)
                self.assertEqual(answer, default_safe_fallback("no_evidence"))
            finally:
                release.set()
                await asyncio.to_thread(finished.wait, 1)
            model.assert_not_awaited()

    async def test_等待同一选择的调用可按自身预算返回且不破坏缓存(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def waiting(**_kwargs):
            entered.set()
            await release.wait()
            return _response('{"locale":"en"}')

        context = FallbackResponseContext("Check this.")
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion", new=AsyncMock(side_effect=waiting)
        ) as model:
            first = asyncio.create_task(
                render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=1)
            )
            try:
                await entered.wait()
                timed_out = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=0.01)
                self.assertEqual(timed_out, default_safe_fallback("no_evidence"))
            finally:
                release.set()
            selected = await first
            cached = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=0)
        model.assert_awaited_once()
        self.assertEqual(selected, get_runtime_prompt_source("safe_fallback.responses.en.no_evidence"))
        self.assertEqual(cached, selected)

    async def test_外部取消必须传播并释放锁(self):
        entered = asyncio.Event()

        async def waiting(**_kwargs):
            entered.set()
            await asyncio.Event().wait()

        context = FallbackResponseContext("Check the weather.")
        with patch("app.services.stream.safe_fallback_response.litellm.acompletion", new=waiting):
            task = asyncio.create_task(
                render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=2)
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with patch(
            "app.services.stream.safe_fallback_response.litellm.acompletion",
            new=AsyncMock(return_value=_response('{"locale":"en"}')),
        ):
            answer = await render_safe_fallback("no_evidence", context=context, model_id="chat", timeout_s=1)
        self.assertEqual(answer, get_runtime_prompt_source("safe_fallback.responses.en.no_evidence"))

    async def test_异常日志不记录用户内容和异常原文(self):
        secret = "用户原始输入不得写入日志"
        with (
            patch(
                "app.services.stream.safe_fallback_response.litellm.acompletion",
                new=AsyncMock(side_effect=RuntimeError(secret)),
            ),
            self.assertLogs("app", level="WARNING") as captured,
        ):
            answer = await render_safe_fallback(
                "no_evidence", context=FallbackResponseContext(secret), model_id="chat", timeout_s=1
            )
        self.assertEqual(answer, default_safe_fallback("no_evidence"))
        self.assertIn("RuntimeError", " ".join(captured.output))
        self.assertNotIn(secret, " ".join(captured.output))

    def test_模板覆盖全部语言且中文默认兼容(self):
        self.assertEqual(
            set(SUPPORTED_FALLBACK_LOCALES),
            {"zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es", "pt", "ru", "ar", "hi"},
        )
        for locale in SUPPORTED_FALLBACK_LOCALES:
            for reason in ("tool_failure", "no_evidence", "protocol_error"):
                text = get_runtime_prompt_source(f"safe_fallback.responses.{locale}.{reason}")
                self.assertTrue(text.strip())
                self.assertNotIn("{{", text)
        self.assertEqual(
            default_safe_fallback("tool_failure"),
            "本次查询仍未完成：查询工具未能取得可用结果，尚未取得足以核实答案的有效来源，因此目前无法可靠给出具体结论。",
        )
        self.assertEqual(
            default_safe_fallback("no_evidence"),
            "本次没能完成所需的查询，暂时无法给出可靠的查询结论。你可以稍后重试，或提供可核验的资料。",
        )
        self.assertEqual(default_safe_fallback("protocol_error"), "本次未能生成可靠的最终答复，请稍后重试。")

    async def test_未知原因属于调用契约错误(self):
        with self.assertRaises(ValueError):
            await render_safe_fallback("invented_reason", context=None, model_id="chat", timeout_s=1)
