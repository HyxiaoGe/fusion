import asyncio
import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite:///./fusion-test.db")
os.environ.setdefault("AUTH_SERVICE_BASE_URL", "http://auth.example:8100")
os.environ.setdefault("AUTH_SERVICE_CLIENT_ID", "fusion-client")
os.environ.setdefault("AUTH_SERVICE_JWKS_URL", "http://auth.example:8100/.well-known/jwks.json")

from app.core.config import settings  # noqa: E402
from app.schemas.response import ApiException  # noqa: E402
from app.services import guest_quota  # noqa: E402
from app.services.chat_service import ChatService  # noqa: E402
from app.services.tool_handlers.base import ToolResult  # noqa: E402
from app.services.tool_handlers.guest_web_search import GuestWebSearchHandler  # noqa: E402

NOW = 1_800_000_000


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))

    def decr(self, key):
        self.ops.append(("decr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key))

    def execute(self):
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.redis.values[key] = self.redis.values.get(key, 0) + 1
                results.append(self.redis.values[key])
            elif op == "decr":
                self.redis.values[key] = self.redis.values.get(key, 0) - 1
                results.append(self.redis.values[key])
            else:
                results.append(True)
        return results


class FakeRedis:
    def __init__(self):
        self.values = {}

    def pipeline(self):
        return FakePipeline(self)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0

    def mget(self, *keys):
        return [self.values.get(key) for key in keys]


def _limits(**overrides):
    values = {
        "GUEST_DAILY_MESSAGES": 20,
        "GUEST_DAILY_MESSAGES_PER_IP": 60,
        "GUEST_DAILY_MESSAGES_GLOBAL": 2000,
        "GUEST_DAILY_SEARCHES": 10,
    }
    values.update(overrides)
    return patch.multiple(settings, **values)


class GuestRunQuotaTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patcher = patch.object(guest_quota, "get_redis", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _user_count(self, user="g1"):
        return self.redis.values.get(f"guest_quota:msg:{guest_quota.china_day(NOW)}:{user}", 0)

    def test_only_one_run_at_a_time_until_released(self):
        with _limits():
            first = guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
            with self.assertRaises(ApiException) as ctx:
                guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
            self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("GUEST_RUN_IN_PROGRESS", 429))
            self.assertEqual(self._user_count(), 1)
            first.release(refund=False)
            guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW).release(refund=False)
        self.assertEqual(self._user_count(), 2)

    def test_daily_message_limit_rejects_without_consuming(self):
        with _limits(GUEST_DAILY_MESSAGES=2):
            for _ in range(2):
                guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW).release(refund=False)
            with self.assertRaises(ApiException) as ctx:
                guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("GUEST_QUOTA_EXHAUSTED", 429))
        self.assertIn("登录后继续", ctx.exception.message)
        self.assertEqual(self._user_count(), 2)
        self.assertFalse(any(":active:" in key for key in self.redis.values))

    def test_ip_and_global_limits_apply_across_guests(self):
        with _limits(GUEST_DAILY_MESSAGES_PER_IP=1):
            guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW).release(refund=False)
            with self.assertRaises(ApiException):
                guest_quota.reserve_guest_run("g2", "1.1.1.1", now=NOW)
            guest_quota.reserve_guest_run("g3", "2.2.2.2", now=NOW).release(refund=False)
        with _limits(GUEST_DAILY_MESSAGES_GLOBAL=2):
            with self.assertRaises(ApiException):
                guest_quota.reserve_guest_run("g4", "3.3.3.3", now=NOW)

    def test_refund_returns_message_and_settles_once(self):
        with _limits():
            reservation = guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
            reservation.release(refund=True)
            reservation.release(refund=True)
        self.assertEqual(self._user_count(), 0)

    def test_redis_failure_is_service_unavailable(self):
        broken = MagicMock()
        broken.set.side_effect = ConnectionError
        with patch.object(guest_quota, "get_redis", return_value=broken):
            with self.assertRaises(ApiException) as ctx:
                guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("GUEST_UNAVAILABLE", 503))

    def test_search_quota_and_usage(self):
        with _limits(GUEST_DAILY_SEARCHES=2):
            self.assertEqual([guest_quota.reserve_guest_search("g1", now=NOW) for _ in range(3)], [True, True, False])
            guest_quota.reserve_guest_run("g1", "1.1.1.1", now=NOW)
            usage = guest_quota.guest_usage("g1", now=NOW)
        self.assertEqual(usage["searches"], {"limit": 2, "used": 2, "remaining": 0})
        self.assertEqual(usage["messages"], {"limit": 20, "used": 1, "remaining": 19})


class GuestRunSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def _settle(self, *, meta=None, error=None):
        release_calls = []
        reservation = guest_quota.GuestRunReservation(user_id="g1", counter_keys=[], slot_token="t")
        reservation.release = lambda *, refund: release_calls.append(refund)

        async def run():
            if error:
                raise error

        task = asyncio.create_task(run())
        with patch("app.services.stream_state_service.get_stream_meta", AsyncMock(return_value=meta)):
            reservation.settle_after(task, conversation_id="c1", task_id="task-1")
            await asyncio.wait(guest_quota._background_tasks)
        return release_calls

    async def test_refund_only_when_run_ends_in_error(self):
        self.assertEqual(await self._settle(meta={"task_id": "task-1", "status": "error"}), [True])
        self.assertEqual(await self._settle(meta={"task_id": "task-1", "status": "done"}), [False])
        self.assertEqual(await self._settle(meta={"task_id": "task-1", "status": "cancelled"}), [False])
        self.assertEqual(await self._settle(meta={"task_id": "other", "status": "error"}), [False])
        self.assertEqual(await self._settle(error=RuntimeError("boom")), [True])


class GuestWebSearchHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_quota_is_reported_to_model_and_hides_tool(self):
        handler = GuestWebSearchHandler("g1")
        with patch("app.services.tool_handlers.guest_web_search.reserve_guest_search", return_value=False):
            result = await handler.execute({"query": "今天的新闻"})
        self.assertEqual(result.status, "failed")
        self.assertIs(result.data["retryable"], False)
        self.assertIn("登录后", handler.format_llm_context(result))
        self.assertTrue(await handler.is_run_budget_exhausted())

    async def test_within_quota_runs_normal_search(self):
        handler = GuestWebSearchHandler("g1")
        expected = ToolResult(status="success", data={"query": "q", "sources": []})
        with (
            patch("app.services.tool_handlers.guest_web_search.reserve_guest_search", return_value=True),
            patch("app.services.tool_handlers.web_search.WebSearchHandler.execute", AsyncMock(return_value=expected)),
        ):
            result = await handler.execute({"query": "q"})
        self.assertIs(result, expected)
        self.assertFalse(await handler.is_run_budget_exhausted())


class GuestModelRestrictionTests(unittest.TestCase):
    def _service(self):
        service = ChatService(MagicMock())
        service.model_control_repository = MagicMock()
        service.model_control_repository.get.return_value = None
        return service

    def test_guest_models_are_first_choice_of_each_auto_provider(self):
        self.assertEqual(guest_quota.guest_model_ids(), ("mimo-v2.6-flash", "deepseek-chat", "qwen3.8-flash"))

    def test_guest_cannot_start_with_other_model(self):
        with self.assertRaises(ApiException) as ctx:
            self._service()._resolve_turn_model(
                "qwen3.8-max", is_new_conversation=True, allowed_model_ids=("deepseek-chat",)
            )
        self.assertEqual((ctx.exception.code, ctx.exception.status_code), ("LOGIN_REQUIRED", 403))

    def test_guest_auto_and_existing_bindings_pick_within_allowed(self):
        service = self._service()
        with patch("app.services.chat_service.pick_auto_model", return_value="deepseek-chat") as pick:
            resolution = service._resolve_turn_model(
                "qwen3.8-max", is_new_conversation=False, allowed_model_ids=("deepseek-chat",)
            )
            self.assertEqual((resolution.model_id, resolution.fallback_from), ("deepseek-chat", "qwen3.8-max"))
            service._resolve_turn_model("auto", is_new_conversation=True, allowed_model_ids=("deepseek-chat",))
        for call in pick.call_args_list:
            self.assertEqual(call.kwargs["allowed_model_ids"], ("deepseek-chat",))


class GuestToolWiringTests(unittest.TestCase):
    def _inputs(self, *, guest):
        from app.services.stream.agent_loop_wiring import AgentLoopRunInput, prepare_agent_loop_call_config_inputs

        run_input = AgentLoopRunInput(
            conversation_id="conv-1",
            user_id="user-1",
            model_id="deepseek-chat",
            litellm_model="openai/deepseek-chat",
            litellm_kwargs={},
            provider="deepseek",
            raw_messages=[],
            has_vision=False,
            file_ids=None,
            original_message="你好",
            assistant_message_id="msg-1",
            task_id="task-1",
            options={},
            capabilities={"functionCalling": True},
            trace_id="run-1",
            knowledge_base_ids=["kb-1"],
            guest=guest,
        )
        dynamic_loader = MagicMock(return_value=SimpleNamespace(definitions=[{"function": {"name": "amap"}}]))
        knowledge_loader = MagicMock(return_value="knowledge")
        dependencies = SimpleNamespace(
            load_dynamic_tools_fn=dynamic_loader,
            load_document_tools_fn=None,
            load_knowledge_tools_fn=knowledge_loader,
            warning_fn=lambda _message: None,
        )
        with patch("app.services.stream.agent_loop_wiring._load_dynamic_tools", side_effect=lambda fn, **kw: fn(**kw)):
            inputs = prepare_agent_loop_call_config_inputs(
                run_input=run_input, db=MagicMock(), dependencies=dependencies
            )
        return inputs, dynamic_loader, knowledge_loader

    def test_guest_gets_only_quota_counted_search(self):
        inputs, dynamic_loader, knowledge_loader = self._inputs(guest=True)
        dynamic_loader.assert_not_called()
        knowledge_loader.assert_not_called()
        self.assertEqual(inputs.additional_tools, [])
        self.assertIsNone(inputs.knowledge_tools)
        self.assertIsInstance(inputs.dynamic_tool_handlers["web_search"], GuestWebSearchHandler)

    def test_registered_user_keeps_mcp_and_knowledge_tools(self):
        inputs, dynamic_loader, knowledge_loader = self._inputs(guest=False)
        dynamic_loader.assert_called_once()
        knowledge_loader.assert_called_once()
        self.assertNotIn("web_search", inputs.dynamic_tool_handlers)


class GuestApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.pop("main", None)
        cls.main = importlib.import_module("main")
        cls.client = TestClient(cls.main.app)
        cls.deps = {}
        for route in cls.main.app.routes:
            pending = list(getattr(getattr(route, "dependant", None), "dependencies", []))
            while pending:
                dep = pending.pop()
                name = getattr(dep.call, "__qualname__", None)
                if name:
                    cls.deps.setdefault(name, dep.call)
                pending.extend(dep.dependencies)

    def tearDown(self):
        self.main.app.dependency_overrides.clear()

    def _as(self, user, chat_service=None):
        self.main.app.dependency_overrides[self.deps["get_current_user"]] = lambda: user
        if chat_service is not None:
            self.main.app.dependency_overrides[self.deps["get_chat_service"]] = lambda: chat_service

    def test_guest_send_failure_refunds_reservation(self):
        service = MagicMock()
        service.process_message = AsyncMock(side_effect=ApiException("LOGIN_REQUIRED", "登录后可使用深度研究", 403))
        reservation = MagicMock()
        self._as(SimpleNamespace(id="g1", is_guest=True, is_superuser=False), service)
        with patch("app.api.chat.reserve_guest_run", return_value=reservation) as reserve:
            response = self.client.post(
                "/api/chat/send",
                json={"model_id": "auto", "message": "hi", "stream": True},
                headers={"CF-Connecting-IP": "1.1.1.1"},
            )
        self.assertEqual(response.status_code, 403)
        reserve.assert_called_once_with("g1", "1.1.1.1")
        self.assertIs(service.process_message.call_args.kwargs["guest_run"], reservation)
        reservation.release.assert_called_once_with(refund=True)

    def test_registered_send_skips_guest_quota(self):
        service = MagicMock()
        service.process_message = AsyncMock(return_value={"ok": True})
        self._as(SimpleNamespace(id="u1", is_guest=False, is_superuser=False), service)
        with patch("app.api.chat.reserve_guest_run") as reserve:
            response = self.client.post("/api/chat/send", json={"model_id": "auto", "message": "hi", "stream": False})
        self.assertEqual(response.status_code, 200)
        reserve.assert_not_called()
        self.assertIsNone(service.process_message.call_args.kwargs["guest_run"])

    def test_entitlements_reflect_tier(self):
        self._as(SimpleNamespace(id="g1", is_guest=True))
        usage = {"messages": {"limit": 20, "used": 3, "remaining": 17}}
        with patch("app.services.guest_quota.guest_usage", return_value=usage):
            data = self.client.get("/api/auth/me/entitlements").json()["data"]
        self.assertEqual(data["tier"], "guest")
        self.assertFalse(data["features"]["image_generation"])
        self.assertEqual(data["quota"], usage)
        self.assertEqual(data["models"], list(guest_quota.guest_model_ids()))
        self._as(SimpleNamespace(id="u1", is_guest=False))
        data = self.client.get("/api/auth/me/entitlements").json()["data"]
        self.assertEqual((data["tier"], data["quota"], data["models"]), ("registered", None, None))
        self.assertTrue(all(data["features"].values()))


if __name__ == "__main__":
    unittest.main()
