import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.stream import llm_stream as llm_stream_module
from app.services.stream.run_output import ConversationStreamOutput


class RecordingOutput:
    def __init__(self, *, owns: bool = True) -> None:
        self.chunks: list[tuple[str, str, str, dict]] = []
        self.owns = owns
        self.ownership_checks = 0

    async def append(self, chunk_type, content, block_id, **extras):
        self.chunks.append((chunk_type, content, block_id, extras))
        return "1-0"

    async def owns_stream(self) -> bool:
        self.ownership_checks += 1
        return self.owns


def _chunk(content: str, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=None,
    )


async def _response(chunks):
    for chunk in chunks:
        yield chunk


class ConversationStreamOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_and_ownership_use_conversation_stream_and_task_lock(self):
        append_chunk = AsyncMock(return_value="9-0")
        check_lock_owner = AsyncMock(return_value=False)
        output = ConversationStreamOutput("conv-1", "task-1")

        with (
            patch("app.services.stream_state_service.append_chunk", append_chunk),
            patch("app.services.stream_state_service.check_lock_owner", check_lock_owner),
        ):
            entry_id = await output.append("answering", "你好", "blk-text", run_id="run-1", step_id="step-1")
            owns = await output.owns_stream()

        self.assertEqual(entry_id, "9-0")
        append_chunk.assert_awaited_once_with(
            "conv-1", "answering", "你好", "blk-text", task_id="task-1", run_id="run-1", step_id="step-1"
        )
        check_lock_owner.assert_awaited_once_with("conv-1", "task-1")
        self.assertFalse(owns)


class StreamRoundOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_deltas_go_to_given_output_without_touching_redis(self):
        output = RecordingOutput()
        with (
            patch("app.services.stream_state_service.append_chunk", AsyncMock(side_effect=AssertionError)),
            patch("app.services.stream_state_service.check_lock_owner", AsyncMock(side_effect=AssertionError)),
        ):
            result = await llm_stream_module.stream_round(
                _response([_chunk("第一段"), _chunk("第二段", finish_reason="stop")]),
                output,
                "conv-1",
                False,
                "blk-thinking",
                "blk-text",
                run_id="run-1",
                step_id="step-1",
            )

        self.assertEqual(result[1], "第一段第二段")
        self.assertEqual(
            output.chunks,
            [
                ("answering", "第一段", "blk-text", {"run_id": "run-1", "step_id": "step-1"}),
                ("answering", "第二段", "blk-text", {"run_id": "run-1", "step_id": "step-1"}),
            ],
        )

    async def test_lost_ownership_cancels_round(self):
        output = RecordingOutput(owns=False)
        chunks = [_chunk("片段") for _ in range(llm_stream_module.LOCK_CHECK_INTERVAL + 5)]

        result = await llm_stream_module.stream_round(
            _response(chunks),
            output,
            "conv-1",
            False,
            "blk-thinking",
            "blk-text",
            run_id="run-1",
            step_id="step-1",
        )

        self.assertEqual(result[3], "cancelled")
        self.assertEqual(output.ownership_checks, 1)
        self.assertEqual(len(output.chunks), llm_stream_module.LOCK_CHECK_INTERVAL)


if __name__ == "__main__":
    unittest.main()
