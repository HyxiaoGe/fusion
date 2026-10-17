"""把研究证据工作集（读取状态清单、来源正文、深研阶段提示）注入模型上下文。"""

from __future__ import annotations

import re

from app.ai.prompts.prompt_message import PromptMessage, ensure_prompt_messages
from app.ai.prompts.section_ids import (
    DEEP_RESEARCH_STAGE,
    RESEARCH_EVIDENCE_WORKSET,
)
from app.services.stream.research_evidence import (
    ResearchEvidenceWorkset,
    build_deep_research_stage_prompt,
    build_research_untrusted_context_messages,
    build_research_workset_prompt,
    count_unattempted_requested_urls,
)


def messages_with_research_workset(
    messages: list[PromptMessage | dict],
    *,
    workset: ResearchEvidenceWorkset,
    deep_research: bool,
    include_candidates: bool = True,
    research_stage: str | None = None,
    allow_plan_update: bool = False,
    terminal_summary: bool = False,
) -> list[PromptMessage]:
    normalized = ensure_prompt_messages(messages)
    if not deep_research:
        if not terminal_summary:
            return normalized
        # 普通请求此前只注入来源正文，不注入读取状态清单，模型的上下文里没有任何一处
        # 说明「本轮实际读到了什么」。它只能从散落在各轮的工具结果自己拼，拼不出来就
        # 填一段合理的叙述——#127 的三个确证样本都是这个形态（声称读了从未发起的读页）。
        # 这里把已有的状态清单一并注入：read_success / read_failed / candidate 逐条列出。
        workset_prompt = build_research_workset_prompt(
            workset,
            include_candidates=True,
            unattempted_request_count=count_unattempted_requested_urls(
                workset,
                _requested_urls_from_messages(normalized),
            ),
        )
        untrusted_messages = build_research_untrusted_context_messages(
            workset,
            include_candidates=True,
        )
        if not workset_prompt and not untrusted_messages:
            return normalized
        normalized = [message for message in normalized if message.section_id != RESEARCH_EVIDENCE_WORKSET]
        insert_at = 0
        while insert_at < len(normalized) and normalized[insert_at].role == "system":
            insert_at += 1
        return [
            *normalized[:insert_at],
            *(
                [PromptMessage(role="system", content=workset_prompt, section_id=RESEARCH_EVIDENCE_WORKSET)]
                if workset_prompt
                else []
            ),
            *untrusted_messages,
            *normalized[insert_at:],
        ]
    stage_prompt = (
        build_deep_research_stage_prompt(research_stage, allow_plan_update=allow_plan_update) if research_stage else ""
    )
    prompt = build_research_workset_prompt(
        workset,
        include_candidates=include_candidates,
    )
    untrusted_messages = build_research_untrusted_context_messages(
        workset,
        include_candidates=include_candidates,
    )
    if not stage_prompt and not prompt and not untrusted_messages:
        return normalized
    normalized = [
        message for message in normalized if message.section_id not in {DEEP_RESEARCH_STAGE, RESEARCH_EVIDENCE_WORKSET}
    ]
    insert_at = 0
    while insert_at < len(normalized) and normalized[insert_at].role == "system":
        insert_at += 1
    return [
        *normalized[:insert_at],
        *([PromptMessage(role="system", content=stage_prompt, section_id=DEEP_RESEARCH_STAGE)] if stage_prompt else []),
        *([PromptMessage(role="system", content=prompt, section_id=RESEARCH_EVIDENCE_WORKSET)] if prompt else []),
        *untrusted_messages,
        *normalized[insert_at:],
    ]


# 用户原文里的 URL 提取。这是词法解析，不是意图判断——与 #132 删除的选包判据性质
# 不同：判断「这句话想不想查证」是猜意图，取出「这句话里有哪些 URL」是 parsing。
_REQUEST_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _requested_urls_from_messages(messages: list[PromptMessage]) -> list[str]:
    urls: list[str] = []
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        urls.extend(_REQUEST_URL_RE.findall(str(getattr(message, "content", "") or "")))
    return urls
