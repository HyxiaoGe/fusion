"""文档草稿流式预览：从流式工具调用参数中增量解出正文，推给前端实时展示。

工具参数按 token 片段到达，整段 JSON 要到本轮结束才完整。这里用保存状态的增量扫描，
只解出顶层字符串字段（title / content），每个片段只扫一次，不随正文增长重扫全文。
草稿只是展示用的临时流，权威内容仍以工具执行后落库的文档版本为准。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

DRAFT_FIELDS = frozenset({"title", "content"})
# 草稿合并推送阈值：约每秒数次刷新，肉眼仍是连续输出。
DRAFT_FLUSH_CHARS = 200
DRAFT_FLUSH_INTERVAL_S = 0.25

_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class TopLevelStringFieldScanner:
    """增量扫描 JSON 对象，产出指定顶层键的字符串值片段（已反转义）。"""

    def __init__(self, fields: frozenset[str]) -> None:
        self._fields = fields
        self._state = "start"
        self._key_buf: list[str] = []
        self._key: str | None = None
        self._escape: str | None = None
        self._high_surrogate: int | None = None
        self._depth = 0
        self._nested_in_string = False
        self._nested_escape = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        value_buf: list[str] = []
        for char in text:
            state = self._state
            if state == "start":
                if char == "{":
                    self._state = "expect_key"
            elif state == "expect_key":
                if char == '"':
                    self._key_buf = []
                    self._state = "key"
                elif char == "}":
                    self._state = "done"
            elif state == "key":
                if self._escape is not None:
                    self._key_buf.append(char)
                    self._escape = None
                elif char == "\\":
                    self._escape = ""
                elif char == '"':
                    self._key = "".join(self._key_buf)
                    self._state = "colon"
                else:
                    self._key_buf.append(char)
            elif state == "colon":
                if char == ":":
                    self._state = "value"
            elif state == "value":
                if char == '"':
                    self._state = "string"
                elif char in "[{":
                    self._depth = 1
                    self._nested_in_string = False
                    self._nested_escape = False
                    self._state = "nested"
                elif not char.isspace():
                    self._state = "scalar"
            elif state == "string":
                piece = self._consume_string_char(char)
                if piece is None:
                    self._flush(out, value_buf)
                    self._state = "after_value"
                elif piece and self._key in self._fields:
                    value_buf.append(piece)
            elif state == "nested":
                self._consume_nested_char(char)
            elif state == "scalar":
                if char == ",":
                    self._state = "expect_key"
                elif char == "}":
                    self._state = "done"
            elif state == "after_value":
                if char == ",":
                    self._state = "expect_key"
                elif char == "}":
                    self._state = "done"
        if self._state == "string":
            self._flush(out, value_buf)
        return out

    def _flush(self, out: list[tuple[str, str]], value_buf: list[str]) -> None:
        if value_buf and self._key is not None:
            out.append((self._key, "".join(value_buf)))
        value_buf.clear()

    def _consume_string_char(self, char: str) -> str | None:
        """返回解码后的片段；None 表示字符串结束。"""

        if self._escape is not None:
            if self._escape == "" and char != "u":
                self._escape = None
                return self._emit_code_point_text(_SIMPLE_ESCAPES.get(char, char))
            self._escape += char
            if len(self._escape) < 5:
                return ""
            hex_digits = self._escape[1:]
            self._escape = None
            try:
                code = int(hex_digits, 16)
            except ValueError:
                return ""
            return self._emit_code_unit(code)
        if char == "\\":
            self._escape = ""
            return ""
        if char == '"':
            self._high_surrogate = None
            return None
        return self._emit_code_point_text(char)

    def _emit_code_unit(self, code: int) -> str:
        if 0xD800 <= code <= 0xDBFF:
            prefix = self._drop_pending_surrogate()
            self._high_surrogate = code
            return prefix
        if 0xDC00 <= code <= 0xDFFF and self._high_surrogate is not None:
            high = self._high_surrogate
            self._high_surrogate = None
            return chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00))
        return self._drop_pending_surrogate() + ("�" if 0xDC00 <= code <= 0xDFFF else chr(code))

    def _emit_code_point_text(self, text: str) -> str:
        return self._drop_pending_surrogate() + text

    def _drop_pending_surrogate(self) -> str:
        if self._high_surrogate is None:
            return ""
        self._high_surrogate = None
        return "�"

    def _consume_nested_char(self, char: str) -> None:
        if self._nested_in_string:
            if self._nested_escape:
                self._nested_escape = False
            elif char == "\\":
                self._nested_escape = True
            elif char == '"':
                self._nested_in_string = False
            return
        if char == '"':
            self._nested_in_string = True
        elif char in "[{":
            self._depth += 1
        elif char in "]}":
            self._depth -= 1
            if self._depth == 0:
                self._state = "after_value"


@dataclass
class _DraftCall:
    draft_id: str
    name: str | None = None
    pending_arguments: str = ""
    started: bool = False
    scanner: TopLevelStringFieldScanner = field(default_factory=lambda: TopLevelStringFieldScanner(DRAFT_FIELDS))
    buffered_field: str | None = None
    buffered_text: str = ""
    last_flush_at: float = 0.0


@dataclass
class DocumentDraftStreamer:
    """把一轮模型输出里文档工具调用的参数片段转成草稿事件。

    非文档工具的调用直接忽略；工具名未到达前的参数先缓存，确认是文档工具后再补扫。
    片段按字数或时间合并后再推送，避免逐 token 写流把断线重放放大成上万条事件；
    末尾残留片段不必补发，工具执行后落库的文档会整体替换草稿。
    """

    tool_names: frozenset[str]
    draft_id_prefix: str
    emit: Callable[[dict[str, Any]], Awaitable[None]]
    clock: Callable[[], float] = time.monotonic
    flush_chars: int = DRAFT_FLUSH_CHARS
    flush_interval_s: float = DRAFT_FLUSH_INTERVAL_S
    _calls: dict[int, _DraftCall] = field(default_factory=dict)

    async def on_tool_call_delta(self, index: int, name: str | None, arguments: str) -> None:
        call = self._calls.get(index)
        if call is None:
            call = _DraftCall(draft_id=f"{self.draft_id_prefix}:{index}")
            self._calls[index] = call
        if name and call.name is None:
            call.name = name
        if call.name is None:
            call.pending_arguments += arguments
            return
        if call.name not in self.tool_names:
            call.pending_arguments = ""
            return
        if not call.started:
            call.started = True
            call.last_flush_at = self.clock()
            await self.emit({"draft_id": call.draft_id, "tool_name": call.name, "phase": "started"})
        text = call.pending_arguments + arguments
        call.pending_arguments = ""
        if not text:
            return
        for field_name, delta in call.scanner.feed(text):
            if call.buffered_field not in (None, field_name):
                await self._flush(call)
            call.buffered_field = field_name
            call.buffered_text += delta
        if len(call.buffered_text) >= self.flush_chars or self.clock() - call.last_flush_at >= self.flush_interval_s:
            await self._flush(call)

    async def _flush(self, call: _DraftCall) -> None:
        call.last_flush_at = self.clock()
        if not call.buffered_text or call.buffered_field is None:
            return
        payload = {
            "draft_id": call.draft_id,
            "tool_name": call.name,
            "field": call.buffered_field,
            "delta": call.buffered_text,
        }
        call.buffered_field = None
        call.buffered_text = ""
        await self.emit(payload)
