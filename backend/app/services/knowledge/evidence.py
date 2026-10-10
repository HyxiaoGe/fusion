"""知识库证据的稳定标识；流式层与工具共用，不依赖检索实现。"""

from __future__ import annotations

import hashlib
from typing import Any

KNOWLEDGE_SEARCH_TOOL_NAME = "knowledge_search"


def knowledge_evidence_id(hit: dict[str, Any]) -> str:
    raw_identity = f"{hit['knowledge_base_id']}:{hit['document_id']}:{hit['index_version']}:{hit['chunk_id']}"
    return f"ev-knowledge-{hashlib.sha256(raw_identity.encode()).hexdigest()[:16]}"
