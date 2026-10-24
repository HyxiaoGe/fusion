import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import inspiration_generator
from app.services.inspiration_catalog import CAPABILITY_BY_KEY, INSPIRATION_CAPABILITIES

NOW = datetime(2026, 10, 12, 5, 0)


def test_parse_keeps_catalog_questions_with_capability_ttl():
    raw = json.dumps(
        {
            "image": ["画一只戴墨镜的柴犬", "", 3, "超" * 41],
            "weather": ["明天成都会下雨吗"],
            "travel": ["不在目录里的分类"],
        },
        ensure_ascii=False,
    )

    items = inspiration_generator.parse_inspirations(raw, now=NOW)

    assert items == [
        {
            "question": "画一只戴墨镜的柴犬",
            "category": "image",
            "expires_at": NOW + timedelta(hours=CAPABILITY_BY_KEY["image"].ttl_hours),
        },
        {
            "question": "明天成都会下雨吗",
            "category": "weather",
            "expires_at": NOW + timedelta(hours=CAPABILITY_BY_KEY["weather"].ttl_hours),
        },
    ]


def test_parse_caps_each_capability_and_accepts_code_fence():
    capability = CAPABILITY_BY_KEY["code"]
    questions = [f"编程题{index}" for index in range(capability.count + 3)]
    raw = "```json\n" + json.dumps({"code": questions}, ensure_ascii=False) + "\n```"

    items = inspiration_generator.parse_inspirations(raw, now=NOW)

    assert [item["question"] for item in items] == questions[: capability.count]


def test_parse_rejects_non_object():
    with pytest.raises(ValueError):
        inspiration_generator.parse_inspirations("[]", now=NOW)


def test_generate_stores_parsed_questions():
    payload = {capability.key: [f"{capability.label}题"] for capability in INSPIRATION_CAPABILITIES}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
    )
    store = AsyncMock(return_value=len(payload))
    headlines = AsyncMock(return_value=["某品牌发布新款折叠屏手机（微博）", "国产大模型更新推理能力（知乎）"])
    with (
        patch.object(inspiration_generator, "fetch_latest_headlines", headlines),
        patch.object(inspiration_generator.litellm, "acompletion", AsyncMock(return_value=response)) as completion,
        patch.object(inspiration_generator, "store_prompt_examples", store),
    ):
        added = asyncio.run(inspiration_generator.generate_daily_inspirations())

    assert added == len(payload)
    assert headlines.await_args.kwargs == {"limit": 60, "scene": "daily_inspiration"}
    prompt = completion.await_args.kwargs["messages"][0]["content"]
    assert "image:" in prompt and "星期" in prompt
    assert "- 某品牌发布新款折叠屏手机（微博）" in prompt
    assert "Base every news and research prompt on one of these headlines" in prompt
    assert "Ask open questions instead" not in prompt
    stored, kwargs = store.await_args.args[0], store.await_args.kwargs
    assert kwargs == {"source": "catalog"}
    assert {item["category"] for item in stored} == set(payload)


def test_generate_falls_back_to_open_questions_when_headlines_unavailable():
    payload = {"news": ["最近科技圈有什么值得关注的事"]}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
    )
    store = AsyncMock(return_value=1)
    with (
        patch.object(
            inspiration_generator, "fetch_latest_headlines", AsyncMock(side_effect=RuntimeError("gateway down"))
        ),
        patch.object(inspiration_generator.litellm, "acompletion", AsyncMock(return_value=response)) as completion,
        patch.object(inspiration_generator, "store_prompt_examples", store),
    ):
        added = asyncio.run(inspiration_generator.generate_daily_inspirations())

    assert added == 1
    prompt = completion.await_args.kwargs["messages"][0]["content"]
    assert "Trending headlines" not in prompt
    assert "Ask open questions instead" in prompt


class _FakeRedis:
    def __init__(self):
        self.keys = set()

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def test_job_runs_once_per_day_and_swallows_errors(monkeypatch):
    redis = _FakeRedis()
    generate = AsyncMock(side_effect=RuntimeError("model down"))
    monkeypatch.setattr(inspiration_generator, "get_redis", lambda: redis)
    monkeypatch.setattr(inspiration_generator, "generate_daily_inspirations", generate)

    asyncio.run(inspiration_generator.generate_daily_inspirations_job())
    asyncio.run(inspiration_generator.generate_daily_inspirations_job())

    assert generate.await_count == 1
