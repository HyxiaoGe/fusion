"""裁判模型：按用例写好的评分标准判定回答，只用于程序判不了的语义要求。

裁判看得到工具结果，才能判断"有没有编造工具结果里没有的事实"；回答与工具结果都已截断。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import litellm
from pydantic import BaseModel, ConfigDict, ValidationError

from app.ai.litellm_utils import merge_extra_body
from app.ai.llm_manager import llm_manager
from app.ai.llm_observability import merge_litellm_kwargs
from app.evals.cases import EvalCase, JudgeCheck
from app.evals.checks import JudgeVerdict

JUDGE_MODEL_ID = "deepseek-chat"
JUDGE_TIMEOUT_SECONDS = 60
JUDGE_MAX_TOKENS = 600

_SYSTEM_PROMPT = """你是对话系统的评测裁判。你只判断一件事：助手的回答是否满足给定的评分标准。

规则：
- 只依据评分标准判定，不评价文风、长短或你自己的偏好。
- 工具结果是助手本轮实际拿到的数据；评分标准涉及事实来源时，以工具结果为准。
- 某条工具结果标注 truncated 时只展示了前面一部分：未展示部分可能含有依据，不能仅因在已展示部分找不到就判为编造。
- 除此之外信息不足以判定时判为不通过，并在理由里说明缺什么。
- 只输出 JSON：{"passed": true 或 false, "reason": "一到两句中文理由"}"""


class _VerdictPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool
    reason: str


def build_judge_messages(check: JudgeCheck, case: EvalCase, snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    tools = [
        {
            "tool": call.get("tool"),
            "status": call.get("status"),
            "arguments": call.get("arguments"),
            "result": call.get("result"),
        }
        for call in snapshot.get("tool_calls") or []
        if call.get("tool") not in {"request_capability", "load_skill", "update_plan", "tool_search"}
    ]
    context = "\n".join(f"- {turn}" for turn in case.setup_turns) or "（无）"
    user = (
        f"## 评分标准\n{check.rubric}\n\n"
        f"## 之前的用户消息\n{context}\n\n"
        f"## 本轮用户消息\n{case.message}\n\n"
        f"## 本轮工具结果\n{json.dumps(tools, ensure_ascii=False, default=str) if tools else '（本轮未调用外部工具）'}\n\n"
        f"## 助手回答\n{snapshot.get('answer_text') or ''}"
    )
    return [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_verdict(content: str) -> JudgeVerdict:
    payload = _VerdictPayload.model_validate_json(content)
    return JudgeVerdict(passed=payload.passed, reason=payload.reason.strip() or "（裁判未给理由）")


async def judge_answer(check: JudgeCheck, case: EvalCase, snapshot: Mapping[str, Any]) -> JudgeVerdict:
    model, _provider, kwargs = llm_manager.resolve_model(JUDGE_MODEL_ID)
    kwargs = dict(kwargs or {})
    merge_extra_body(kwargs, {"thinking": {"type": "disabled"}})
    messages = build_judge_messages(check, case, snapshot)

    async def ask() -> JudgeVerdict:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            stream=False,
            temperature=0,
            max_tokens=JUDGE_MAX_TOKENS,
            timeout=JUDGE_TIMEOUT_SECONDS,
            response_format={"type": "json_object"},
            **merge_litellm_kwargs("eval_judge", kwargs),
        )
        return parse_verdict(response.choices[0].message.content or "")

    try:
        return await ask()
    except ValidationError:
        # 实测偶发漏掉 passed 字段（门禁 run 37210651726）；只对格式错误重问一次。
        return await ask()
