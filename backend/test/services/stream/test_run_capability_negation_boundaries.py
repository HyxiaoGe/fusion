"""#129：授权否定只匹配真正的指令，不截取普通词里的「别」。"""

import pytest

from app.services.stream import run_capability_request_signals as signals
from app.services.stream.dynamic_tool_discovery import resolve_discovery_network_denials
from app.services.stream.run_capability_router import _classify_literal_layer

_COMPOUNDS = ("分别", "个别", "特别", "区别", "差别", "性别", "级别", "辨别", "识别")
_ACTIONS = ("打开", "读取", "阅读", "访问", "浏览", "搜索")
_DENIALS = (
    ("别打开那个链接", (False, True, False)),
    ("不要联网", (True, True, True)),
    ("别用 web_search", (True, False, False)),
    ("请勿访问", (False, True, False)),
    ("不要再搜索了", (True, False, False)),
    ("禁止读取网页", (False, True, False)),
    ("请别打开那个链接", (False, True, False)),
    ("先别搜索", (True, False, False)),
    ("不要把 web_search 用于这次回答", (True, False, False)),
    ("请勿在本次任务中调用 url_read", (False, True, False)),
    ("禁止让模型调用 web_search", (True, False, False)),
    ("严禁再次使用 url_read", (False, True, False)),
)


@pytest.mark.parametrize("compound", _COMPOUNDS)
@pytest.mark.parametrize("action", _ACTIONS)
def test_common_compound_words_do_not_revoke_network_tools(compound: str, action: str) -> None:
    message = f"请{compound}{action}这两项"
    request = signals._extract_request_signals(message)
    expected = (False, False, False)
    assert (request.web_search_denied, request.url_read_denied, request.all_network_denied) == expected
    assert resolve_discovery_network_denials(message) == expected


@pytest.mark.parametrize(
    "message",
    (
        "请分别打开这两个链接",
        "请个别打开几个页面",
        "特别打开这一页看看",
        "请区别打开测试页和正式页",
        "请分别阅读这两页",
        "请分别访问这两个站点",
        "请分别浏览一下",
        "请分别读取这两份文档",
        "分别搜索这两个关键词",
        "请个别搜索一下",
        "特别搜索最新消息",
        "请逐一打开这两个链接",
        "请依次阅读这两页",
    ),
)
def test_issue_examples_keep_both_paths_authorized(message: str) -> None:
    request = signals._extract_request_signals(message)
    assert (request.web_search_denied, request.url_read_denied, request.all_network_denied) == (False, False, False)
    assert resolve_discovery_network_denials(message) == (False, False, False)


@pytest.mark.parametrize(("message", "expected"), _DENIALS)
def test_explicit_network_denials_still_apply_to_both_paths(message: str, expected: tuple[bool, bool, bool]) -> None:
    request = signals._extract_request_signals(message)
    assert (request.web_search_denied, request.url_read_denied, request.all_network_denied) == expected
    assert resolve_discovery_network_denials(message) == expected


@pytest.mark.parametrize(
    ("pattern", "ordinary", "prohibition"),
    (
        (signals._NEGATED_ALL_NETWORK_RE, "分别联网", "别联网"),
        (signals._NEGATED_WEB_SEARCH_RE, "分别搜索", "别搜索"),
        (signals._NEGATED_URL_READ_RE, "分别打开", "别打开"),
        (signals._NEGATED_VERIFIED_WEB_RE, "分别查证", "别查证"),
        (signals._NEGATED_WEB_TOOL_NAME_RE, "特别调用 web_search", "别用 web_search"),
        (signals._NEGATED_URL_TOOL_NAME_RE, "类别调用 url_read", "别用 url_read"),
    ),
)
def test_every_denial_pattern_has_a_word_boundary(pattern, ordinary: str, prohibition: str) -> None:
    assert pattern.search(ordinary) is None
    assert pattern.search(prohibition) is not None


def test_search_object_retains_the_compound_word() -> None:
    assert "分别" in signals._extract_web_request_object("分别搜索这两个关键词")


def test_explicit_urls_keep_the_legacy_reader_and_discovery_authorization() -> None:
    message = "请分别打开 https://example.com/ 和 https://httpbin.org/html，并分别说明页面内容。"
    request = signals._extract_request_signals(message)
    route = _classify_literal_layer(request, ["web_search", "url_read"])
    assert route is not None and route.package_id == "url_read"
    assert request.url_read_request is True
    assert resolve_discovery_network_denials(message) == (False, False, False)


@pytest.mark.parametrize(
    "message",
    (
        "不用功调用 web_search",
        "不用户调用 url_read",
        "不要紧调用 web_search",
        "不得不调用 url_read",
        "不可避免地调用 web_search",
    ),
)
def test_other_negation_prefixes_do_not_reach_across_unrelated_words(message: str) -> None:
    request = signals._extract_request_signals(message)
    assert (request.web_search_denied, request.url_read_denied, request.all_network_denied) == (False, False, False)
    assert resolve_discovery_network_denials(message) == (False, False, False)
