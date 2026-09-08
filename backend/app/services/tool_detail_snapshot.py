"""工具详情的独立快照：保留业务数据，仅遮盖凭据并标明截断。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote_plus

_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_OMITTED = object()
_MAX_STRING_CHARS = 32 * 1024
_MAX_ITEMS = 200
_MAX_DEPTH = 12
_MAX_NODES = 2000
_CREDENTIAL_KEYS = {
    "apikey",
    "authorization",
    "proxyauthorization",
    "cookie",
    "setcookie",
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "password",
    "passwd",
    "privatekey",
    "clientsecret",
    "secret",
    "secretkey",
    "credential",
    "credentials",
    "sessiontoken",
    "sessionid",
    "awssecretaccesskey",
}
_CREDENTIAL_QUERY_KEYS = _CREDENTIAL_KEYS | {
    "key",
    "sig",
    "signature",
    "xamzsignature",
    "xamzcredential",
    "xamzsecuritytoken",
}
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TOKEN_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
    re.compile(r"\bxox[A-Za-z0-9]-[A-Za-z0-9-]{8,}\b"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\Z)",
        re.DOTALL,
    ),
)
_INLINE_CREDENTIAL_RE = re.compile(
    r"(?<![\w-])((?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|session[_-]?(?:id|token)|token|password|passwd|private[_-]?key|"
    r"secret|authorization|proxy[_-]?authorization)[\"']?\s*[:=]\s*)"
    r"(\"[^\"\r\n]*(?:\"|$)|'[^'\r\n]*(?:'|$)|(?:Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+|[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_INLINE_COOKIE_RE = re.compile(
    r"(?<![\w-])((?:cookie|set[_-]?cookie)[\"']?\s*[:=]\s*)"
    r"(\"[^\"\r\n]*(?:\"|$)|'[^'\r\n]*(?:'|$)|[A-Za-z0-9._~+/=-]+(?:[ \t]*;[ \t]*[A-Za-z0-9._~+/=-]+)*)",
    re.IGNORECASE,
)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


@dataclass
class _FieldPaths:
    values: set[str] = field(default_factory=set)

    def add(self, path: str) -> None:
        path = path.encode("utf-8")[:128].decode("utf-8", errors="ignore")
        while _json_size(path) > 128:
            path = path[:-1]
        if len(self.values) >= 64 and path not in self.values:
            # 汇总到所属区段，避免字段路径本身撑大快照。
            self.values = {value.split(".", 1)[0].split("[", 1)[0] for value in self.values}
        self.values.add(path)


def _mask_query(value: str) -> str:
    items = []
    for item in value.split("&"):
        key, separator, _ = item.partition("=")
        if separator and _normalize_key(unquote_plus(key)) in _CREDENTIAL_QUERY_KEYS:
            item = f"{key}={_REDACTED}"
        items.append(item)
    return "&".join(items)


def _mask_url(match: re.Match[str]) -> str:
    value = re.sub(r"^(https?://)[^/?#]*@", r"\1", match.group(), flags=re.IGNORECASE)
    base, fragment_separator, fragment = value.partition("#")
    path, query_separator, query = base.partition("?")
    return path + query_separator + _mask_query(query) + fragment_separator + _mask_query(fragment)


def _mask_text(value: str) -> str:
    value = _URL_RE.sub(_mask_url, value)
    for pattern in _TOKEN_PATTERNS:
        value = pattern.sub(_REDACTED, value)
    for pattern in (_INLINE_COOKIE_RE, _INLINE_CREDENTIAL_RE):
        value = pattern.sub(_mask_inline_credential, value)
    return value


def _mask_inline_credential(match: re.Match[str]) -> str:
    original = match.group(2)
    quote = original[0] if original[0] in {'"', "'"} else ""
    return match.group(1) + quote + _REDACTED + quote


@dataclass
class _SnapshotCopy:
    remaining_bytes: int
    redacted: _FieldPaths
    truncated: _FieldPaths
    remaining_nodes: int = _MAX_NODES

    def copy(self, value: Any, path: str, depth: int = 0) -> Any:
        self.remaining_nodes -= 1
        if depth > _MAX_DEPTH or self.remaining_nodes < 0:
            self.truncated.add(path)
            return self.copy_scalar(_TRUNCATED, path)
        if isinstance(value, dict):
            return self.copy_dict(value, path, depth)
        if isinstance(value, list):
            return self.copy_list(value, path, depth)
        if isinstance(value, str):
            if len(value) > _MAX_STRING_CHARS:
                self.truncated.add(path)
            # 预读边界后的少量字符，避免先截断导致密钥形态匹配失效。
            value = value[: _MAX_STRING_CHARS + 256]
            masked = _mask_text(value)
            if masked != value:
                self.redacted.add(path)
            value = masked[:_MAX_STRING_CHARS]
        return self.copy_scalar(value, path)

    def copy_scalar(self, value: Any, path: str) -> Any:
        if _json_size(value) > self.remaining_bytes:
            self.truncated.add(path)
            if not isinstance(value, str) or self.remaining_bytes < _json_size("…"):
                return _OMITTED
            low, high = 0, len(value)
            while low < high:
                middle = (low + high + 1) // 2
                if _json_size(value[:middle] + "…") <= self.remaining_bytes:
                    low = middle
                else:
                    high = middle - 1
            value = value[:low] + "…"
        self.remaining_bytes -= _json_size(value)
        return value

    def copy_dict(self, value: dict[str, Any], path: str, depth: int) -> Any:
        if self.remaining_bytes < 2:
            self.truncated.add(path)
            return _OMITTED
        self.remaining_bytes -= 2
        copied: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            cost = _json_size(key) + 4
            if index >= _MAX_ITEMS or len(key.encode("utf-8")) > 256 or cost + 2 > self.remaining_bytes:
                self.truncated.add(path)
                break
            child_path = f"{path}.{key}"
            self.remaining_bytes -= cost
            if _normalize_key(key) in _CREDENTIAL_KEYS:
                self.redacted.add(child_path)
                child = _REDACTED
            child_copy = self.copy(child, child_path, depth + 1)
            if child_copy is _OMITTED:
                self.truncated.add(path)
                break
            copied[key] = child_copy
        return copied

    def copy_list(self, value: list[Any], path: str, depth: int) -> Any:
        if self.remaining_bytes < 2:
            self.truncated.add(path)
            return _OMITTED
        self.remaining_bytes -= 2
        copied = []
        for index, child in enumerate(value):
            if index >= _MAX_ITEMS or self.remaining_bytes < 4:
                self.truncated.add(path)
                break
            self.remaining_bytes -= 2
            child_copy = self.copy(child, f"{path}[{index}]", depth + 1)
            if child_copy is _OMITTED:
                self.truncated.add(path)
                break
            copied.append(child_copy)
        return copied


def build_tool_detail_snapshot(payload: dict, result: dict, error: str | None = None) -> dict:
    """复制实际工具输入输出；分区预算为 24/72/4 KiB，总快照不超过 128 KiB。"""
    redacted, truncated = _FieldPaths(), _FieldPaths()
    snapshot: dict[str, Any] = {"schema_version": 1}
    for name, value, budget in (("payload", payload, 24), ("result", result, 72), ("error", error, 4)):
        snapshot[name] = _SnapshotCopy(budget * 1024, redacted, truncated).copy(value, name)
    snapshot["redacted_fields"] = sorted(redacted.values)
    snapshot["truncated_fields"] = sorted(truncated.values)
    return snapshot


def build_tool_observation_snapshot(text: str, *, step_number: int) -> dict:
    """只复制已经回填的工具消息；正文最多 48 KiB，单字符串最多 32K 字符。"""
    redacted, truncated = _FieldPaths(), _FieldPaths()
    safe_text = _SnapshotCopy(48 * 1024, redacted, truncated).copy(text, "observation")
    return {
        "schema_version": 1,
        "status": "available",
        "text": safe_text,
        "original_chars": len(text),
        "generated_round_index": step_number,
        "redacted_fields": sorted(redacted.values),
        "truncated_fields": sorted(truncated.values),
    }
