"""工具失败或降级后替代网页证据的运行期登记。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.services.search_budget import MAX_CONTEXT_SOURCES
from app.services.source_evidence_ledger import canonicalize_evidence_url

_URL_PATTERN = re.compile(r"https?://[^\s\])}>\"'，。；、]+", re.IGNORECASE)


@dataclass
class RecoveryEvidenceWorkset:
    """只登记本轮运行实际获取到非空内容的来源身份，不复制网页正文。"""

    source_keys: set[tuple[str, str]] = field(default_factory=set)

    def record_result(self, tool_name: str, result: Any) -> None:
        if _value(result, "status") != "success":
            return
        data = _value(result, "data")
        if not isinstance(data, dict):
            return
        if tool_name == "web_search":
            sources = data.get("sources") or []
            for source in sources[: _injected_source_count(data)]:
                if _has_content(_value(source, "content")) or _has_content(_value(source, "description")):
                    self._record("search", _value(source, "url"))
        elif tool_name == "url_read" and _has_content(data.get("content")):
            self._record("url_read", data.get("url"))

    def _record(self, kind: str, raw_url: Any) -> None:
        if url := _canonical_url(raw_url):
            self.source_keys.add((kind, url))


def _injected_source_count(data: dict[str, Any]) -> int:
    # 只登记已注入模型的正文摘要；额外候选只有链接身份，读页成功后才可作为事实依据。
    limits = []
    for key in ("context_source_count", "context_source_limit"):
        if key not in data:
            continue
        value = data[key]
        limits.append(value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0)
    return min(limits) if limits else MAX_CONTEXT_SOURCES


def has_recovery_evidence(content_blocks: list[Any], *, evidence: RecoveryEvidenceWorkset) -> bool:
    """要求当前来源块与成功获取的实际内容匹配，元数据本身不构成证据。"""
    return bool(_eligible_blocks(content_blocks, evidence))


def is_grounded_recovery_answer(
    answer: str,
    content_blocks: list[Any],
    *,
    evidence: RecoveryEvidenceWorkset,
) -> bool:
    """判断非空答复是否有实际网页证据，不验证逐项事实或引用正确性。

    引用缺失或格式错误不等于没有取得来源，不能据此丢弃正常答复。
    调用方须保留成功产品结果、知识库和深度研究各自的校验边界。
    """
    if not isinstance(answer, str) or not answer.strip():
        return False
    return has_recovery_evidence(content_blocks, evidence=evidence)


def _eligible_blocks(content_blocks: list[Any], evidence: RecoveryEvidenceWorkset) -> list[dict[str, Any]]:
    blocks = []
    for block in content_blocks:
        kind = _value(block, "type")
        if kind not in {"search", "url_read"} or _value(block, "status") != "success":
            continue
        refs = []
        for ref in _value(block, "source_refs") or []:
            url = _canonical_url(_value(ref, "url"))
            if _value(ref, "status") != "success" or (kind, url) not in evidence.source_keys:
                continue
            refs.append(
                {
                    "kind": kind,
                    "url": url,
                    "status": "success",
                    "title": _value(ref, "title") or "",
                    "citation_index": _value(ref, "citation_index"),
                }
            )
        if refs:
            blocks.append({"type": kind, "status": "success", "source_refs": refs})
    return blocks


def _has_content(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    # 只有链接或空白仍属于来源元数据，不能证明曾获取到内容。
    return bool(_URL_PATTERN.sub("", value).strip())


def _canonical_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        return canonicalize_evidence_url(value)
    except ValueError:
        return ""


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
