"""每天按功能目录为首页「今日灵感」出题。

用辅助模型按 inspiration_catalog 的每个功能出当天的题，写入示例池；每道题按功能设有效期，
天气、新闻这类时效题第二天就下架。每天凌晨跑一次，多个 worker 用 Redis 锁保证同一天只跑一份。
出题失败只记日志，池子里还没过期的题照常展示。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

import litellm

from app.ai.llm_observability import merge_litellm_kwargs
from app.ai.prompts import prompt_manager
from app.core.revocation import get_redis
from app.services.inspiration_catalog import INSPIRATION_CAPABILITIES
from app.services.prompt_examples_service import store_prompt_examples
from app.services.utility_model import UTILITY_MODEL_ID, resolve_utility_model

logger = logging.getLogger(__name__)

_CHINA_TZ = timezone(timedelta(hours=8))
_WEEKDAYS = "一二三四五六日"
# 后台任务不在请求链路上，不受 10s 中间件限制；一次要出几十道题，给足时间和输出预算。
_TIMEOUT_SECONDS = 90
_MAX_TOKENS = 2048
_MAX_QUESTION_CHARS = 40
_LOCK_TTL_SECONDS = 6 * 3600


def _capabilities_text() -> str:
    return "\n".join(
        f"- {capability.key}: {capability.guidance}; write {capability.count}"
        for capability in INSPIRATION_CAPABILITIES
    )


def parse_inspirations(raw: str, *, now: datetime) -> list[dict]:
    """把模型返回的 JSON 转成入池条目；不认识的功能、超出数量或过长的题直接丢弃。"""

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("今日灵感返回的不是 JSON 对象")

    items: list[dict] = []
    for capability in INSPIRATION_CAPABILITIES:
        questions = data.get(capability.key)
        if not isinstance(questions, list):
            continue
        expires_at = now + timedelta(hours=capability.ttl_hours)
        accepted = 0
        for question in questions:
            if accepted >= capability.count:
                break
            if not isinstance(question, str):
                continue
            question = question.strip()
            if not question or len(question) > _MAX_QUESTION_CHARS:
                continue
            items.append({"question": question, "category": capability.key, "expires_at": expires_at})
            accepted += 1
    return items


async def generate_daily_inspirations(*, now: datetime | None = None) -> int:
    """出一轮题并入池，返回新增条数。"""

    current = (now or datetime.now(_CHINA_TZ)).astimezone(_CHINA_TZ).replace(tzinfo=None)
    today: date = current.date()
    prompt, prompt_metadata = prompt_manager.format_prompt_with_metadata(
        "generate_inspirations",
        today=f"{today.isoformat()} 星期{_WEEKDAYS[today.weekday()]}",
        capabilities=_capabilities_text(),
    )
    model, _, kwargs = resolve_utility_model(UTILITY_MODEL_ID)
    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        max_tokens=_MAX_TOKENS,
        timeout=_TIMEOUT_SECONDS,
        **merge_litellm_kwargs("generate_inspirations", kwargs, prompt_metadata=prompt_metadata),
    )
    items = parse_inspirations(response.choices[0].message.content or "", now=current)
    if not items:
        raise ValueError("今日灵感没有可用的题")
    return await store_prompt_examples(items, source="catalog")


async def generate_daily_inspirations_job() -> None:
    """定时任务入口：拿到当天的锁才执行，失败只记日志。"""

    try:
        day = datetime.now(_CHINA_TZ).strftime("%Y%m%d")
        if not await asyncio.to_thread(get_redis().set, f"inspiration:lock:{day}", "1", nx=True, ex=_LOCK_TTL_SECONDS):
            return
        added = await generate_daily_inspirations()
        logger.info("inspiration_generated added=%s", added)
    except Exception:
        logger.warning("inspiration_generation_failed", exc_info=True)
