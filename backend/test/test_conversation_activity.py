"""对话活动登记：进行中 / 完成未读两个按用户的集合随流生命周期变化。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis.aioredis


class ConversationActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.patchers = [
            patch("app.services.stream_state_service.get_redis_pool", return_value=self.fake_redis),
            patch("app.services.conversation_activity_service.get_redis_pool", return_value=self.fake_redis),
        ]
        for patcher in self.patchers:
            patcher.start()

    async def asyncTearDown(self):
        await self.fake_redis.flushall()
        await self.fake_redis.aclose()

    def tearDown(self):
        for patcher in self.patchers:
            patcher.stop()

    async def _activity(self, user_id="user-1"):
        from app.services.conversation_activity_service import get_conversation_activity

        return await get_conversation_activity(user_id)

    async def test_running_stream_is_listed_and_success_marks_unread(self):
        from app.services.stream_state_service import finalize_stream, init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")
        self.assertEqual(await self._activity(), {"streaming": ["conv-1"], "unread": []})

        self.assertTrue(await finalize_stream("conv-1", success=True, task_id="task-1"))
        self.assertEqual(await self._activity(), {"streaming": [], "unread": ["conv-1"]})

    async def test_failed_stream_also_marks_unread(self):
        from app.services.stream_state_service import finalize_stream, init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")
        await finalize_stream("conv-1", success=False, error_msg="boom", task_id="task-1")

        self.assertEqual(await self._activity(), {"streaming": [], "unread": ["conv-1"]})

    async def test_user_stop_does_not_mark_unread(self):
        from app.services.stream_state_service import cancel_stream, init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")
        self.assertTrue(await cancel_stream("conv-1", "msg-1", "task-1"))

        self.assertEqual(await self._activity(), {"streaming": [], "unread": []})

    async def test_superseded_finalize_keeps_new_run_active(self):
        from app.services.stream_state_service import finalize_stream, init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")
        await init_stream("conv-1", "user-1", "gpt-4", "msg-2", "task-2")
        # 旧任务锁已被新任务覆盖，收尾被 Lua 拒绝，不能把新运行移出进行中。
        self.assertFalse(await finalize_stream("conv-1", success=True, task_id="task-1"))

        self.assertEqual(await self._activity(), {"streaming": ["conv-1"], "unread": []})

    async def test_new_run_and_read_clear_unread(self):
        from app.services.conversation_activity_service import mark_conversation_read
        from app.services.stream_state_service import finalize_stream, init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")
        await finalize_stream("conv-1", success=True, task_id="task-1")
        await init_stream("conv-1", "user-1", "gpt-4", "msg-2", "task-2")
        self.assertEqual(await self._activity(), {"streaming": ["conv-1"], "unread": []})

        await finalize_stream("conv-1", success=True, task_id="task-2")
        await mark_conversation_read("user-1", "conv-1")
        self.assertEqual(await self._activity(), {"streaming": [], "unread": []})

    async def test_stale_active_entry_is_dropped_on_read(self):
        # 进程在收尾前退出：集合里留着条目，但 meta 已过期。
        await self.fake_redis.sadd("stream:user_active:user-1", "conv-gone")

        self.assertEqual(await self._activity(), {"streaming": [], "unread": []})
        self.assertEqual(await self.fake_redis.smembers("stream:user_active:user-1"), set())

    async def test_users_are_isolated(self):
        from app.services.stream_state_service import init_stream

        await init_stream("conv-1", "user-1", "gpt-4", "msg-1", "task-1")

        self.assertEqual(await self._activity("user-2"), {"streaming": [], "unread": []})


class ConversationActivityRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_activity_route_is_registered_before_conversation_detail(self):
        from app.api.chat import router

        paths = [(route.path, sorted(route.methods)) for route in router.routes]
        activity = paths.index(("/conversations/activity", ["GET"]))
        detail = paths.index(("/conversations/{conversation_id}", ["GET"]))
        self.assertLess(activity, detail)

    async def test_activity_endpoint_returns_503_when_redis_fails(self):
        from app.api.chat import get_conversations_activity
        from app.schemas.response import ApiException

        with patch(
            "app.api.chat.get_conversation_activity",
            side_effect=RuntimeError("redis down"),
        ):
            with self.assertRaises(ApiException) as raised:
                await get_conversations_activity(
                    request=SimpleNamespace(state=SimpleNamespace(request_id="req-1")),
                    current_user=SimpleNamespace(id="user-1"),
                )
        self.assertEqual(raised.exception.status_code, 503)
