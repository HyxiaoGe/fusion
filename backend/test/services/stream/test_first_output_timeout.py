import asyncio
import unittest
from types import SimpleNamespace

from app.services.stream.first_output_timeout import (
    FirstOutputTimeoutError,
    StreamIdleTimeoutError,
    await_first_output,
    with_stream_idle_timeout,
)


def _chunk(*, content=None, reasoning=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


class _Stream:
    def __init__(self, chunks, *, delays=None):
        self._chunks = list(chunks)
        self._delays = list(delays or [0] * len(self._chunks))
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        await asyncio.sleep(self._delays.pop(0))
        return self._chunks.pop(0)

    async def aclose(self):
        self.closed = True


async def _collect(response):
    return [chunk async for chunk in response]


class AwaitFirstOutputTest(unittest.IsolatedAsyncioTestCase):
    async def test_replays_all_chunks_when_output_arrives_in_time(self):
        role_chunk = _chunk()
        chunks = [role_chunk, _chunk(reasoning="想"), _chunk(content="答"), _chunk(finish_reason="stop")]
        stream = _Stream(chunks)
        wrapped = []

        async def open_response():
            return stream

        def wrap(response):
            wrapped.append(response)
            return response

        response = await await_first_output(open_response, wrap_response=wrap, timeout_s=1, model_id="m")

        self.assertEqual(await _collect(response), chunks)
        self.assertEqual(wrapped, [stream])

    async def test_role_only_chunks_do_not_count_as_output(self):
        stream = _Stream([_chunk(), _chunk(content="迟到")], delays=[0, 0.2])

        async def open_response():
            return stream

        with self.assertRaises(FirstOutputTimeoutError) as raised:
            await await_first_output(open_response, wrap_response=lambda r: r, timeout_s=0.05, model_id="slow")

        self.assertEqual(raised.exception.model_id, "slow")
        self.assertTrue(stream.closed)

    async def test_slow_request_open_counts_toward_timeout(self):
        async def open_response():
            await asyncio.sleep(0.2)
            return _Stream([_chunk(content="x")])

        with self.assertRaises(FirstOutputTimeoutError):
            await await_first_output(open_response, wrap_response=lambda r: r, timeout_s=0.05, model_id="m")

    async def test_empty_stream_is_returned_without_error(self):
        async def open_response():
            return _Stream([])

        response = await await_first_output(open_response, wrap_response=lambda r: r, timeout_s=1, model_id="m")

        self.assertEqual(await _collect(response), [])


class StreamIdleTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_stall_after_first_output_raises_and_closes_request(self):
        first, second = _chunk(content="上海三日游："), _chunk(content="第一天")
        stream = _Stream([first, second, _chunk(content="迟到")], delays=[0, 0, 0.2])

        async def open_response():
            return stream

        response = await await_first_output(
            open_response, wrap_response=lambda r: r, timeout_s=1, model_id="stuck", idle_timeout_s=0.05
        )
        received = []
        with self.assertRaises(StreamIdleTimeoutError) as raised:
            async for chunk in response:
                received.append(chunk)

        self.assertEqual(received, [first, second])
        self.assertEqual(raised.exception.model_id, "stuck")
        self.assertTrue(stream.closed)

    async def test_steady_stream_is_not_cut_even_when_total_time_exceeds_idle_limit(self):
        chunks = [_chunk(content=str(index)) for index in range(6)]
        stream = _Stream(chunks, delays=[0.02] * 6)

        async def open_response():
            return stream

        response = await await_first_output(
            open_response, wrap_response=lambda r: r, timeout_s=1, model_id="m", idle_timeout_s=0.05
        )

        self.assertEqual(await _collect(response), chunks)
        self.assertFalse(stream.closed)

    async def test_upstream_timeout_error_is_not_reported_as_stall(self):
        class UpstreamTimeout(TimeoutError):
            pass

        class _FailingStream(_Stream):
            async def __anext__(self):
                if self._chunks:
                    return self._chunks.pop(0)
                raise UpstreamTimeout("read timeout")

        async def open_response():
            return _FailingStream([_chunk(content="x")])

        response = await await_first_output(
            open_response, wrap_response=lambda r: r, timeout_s=1, model_id="m", idle_timeout_s=5
        )
        with self.assertRaises(UpstreamTimeout):
            await _collect(response)

    async def test_round_without_first_output_limit_still_gets_idle_limit(self):
        stream = _Stream([_chunk(content="a"), _chunk(content="b")], delays=[0, 0.2])
        response = with_stream_idle_timeout(stream, timeout_s=0.05, model_id="fallback")

        with self.assertRaises(StreamIdleTimeoutError):
            await _collect(response)
        self.assertTrue(stream.closed)

        non_stream = object()
        self.assertIs(with_stream_idle_timeout(non_stream, timeout_s=0.05, model_id="m"), non_stream)
