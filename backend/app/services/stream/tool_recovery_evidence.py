"""工具失败后替代网页证据的运行期登记与显式引用门禁。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.services.final_answer_evidence import build_used_final_answer_evidence
from app.services.source_evidence_ledger import canonicalize_evidence_url

_CITATION_PATTERN = re.compile(r"(?:\[(\d{1,3})\]|⟦(\d{1,3})⟧)")
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
            for source in data.get("sources") or []:
                if _has_content(_value(source, "content")) or _has_content(_value(source, "description")):
                    self._record("search", _value(source, "url"))
        elif tool_name == "url_read" and _has_content(data.get("content")):
            self._record("url_read", data.get("url"))

    def _record(self, kind: str, raw_url: Any) -> None:
        if url := _canonical_url(raw_url):
            self.source_keys.add((kind, url))


def has_recovery_evidence(content_blocks: list[Any], *, evidence: RecoveryEvidenceWorkset) -> bool:
    """要求当前来源块与成功获取的实际内容匹配，元数据本身不构成证据。"""
    return bool(_eligible_blocks(content_blocks, evidence))


def is_grounded_recovery_answer(
    answer: str,
    content_blocks: list[Any],
    *,
    evidence: RecoveryEvidenceWorkset,
) -> bool:
    """验证来源存在且被显式引用；不声称能机械证明答案每个语义事实。

    调用方仍须保证发生过产品工具失败，且没有成功产品结果需要原校验。
    无证据时的诚实失败收尾应走独立分支，不能通过此门禁。
    """
    if not isinstance(answer, str) or not answer.strip():
        return False
    blocks = _eligible_blocks(content_blocks, evidence)
    refs = [ref for block in blocks for ref in block["source_refs"]]
    allowed_indexes = {ref["citation_index"] for ref in refs if _valid_index(ref.get("citation_index"))}
    citations = {int(match.group(1) or match.group(2)) for match in _CITATION_PATTERN.finditer(answer)}
    if not citations.issubset(allowed_indexes):
        return False
    mentioned_urls = {_canonical_url(url) for url in _URL_PATTERN.findall(answer)}
    allowed_urls = {ref["url"] for ref in refs}
    if not mentioned_urls.issubset(allowed_urls):
        return False
    used = build_used_final_answer_evidence(
        content_blocks=blocks,
        answer_text=answer,
        evidence_policy="deep_research_v1",
        allowed_citation_indexes=allowed_indexes,
    )
    return bool(used or mentioned_urls)


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


def _valid_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
