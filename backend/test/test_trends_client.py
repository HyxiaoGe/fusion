import json

import pytest

from app.services.external.trends_client import TrendHeadlinesError, parse_headlines


def test_parse_headlines_dedupes_titles_and_keeps_platform():
    text = json.dumps(
        {
            "success": True,
            "data": [
                {"title": "新款折叠屏手机发布", "platform_name": "微博", "rank": 1},
                {"title": "新款折叠屏手机发布", "platform_name": "知乎", "rank": 2},
                {"title": "  ", "platform_name": "百度热搜"},
                {"title": "国产大模型更新", "platform_name": ""},
                "not-a-dict",
            ],
        },
        ensure_ascii=False,
    )

    assert parse_headlines(text) == ["新款折叠屏手机发布（微博）", "国产大模型更新"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"success": False, "error": "no data"}),
        json.dumps({"success": True, "data": "oops"}),
        json.dumps({"success": True, "data": [{"title": ""}]}),
    ],
)
def test_parse_headlines_rejects_unusable_payloads(text):
    with pytest.raises(TrendHeadlinesError):
        parse_headlines(text)
