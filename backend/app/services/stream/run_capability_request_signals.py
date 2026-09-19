"""抽取 Run 请求信号并解析联网授权作用域，不决定能力包。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TRANSFORM_RE = re.compile(
    r"翻译|译成|改写|重写|润色|措辞|"
    r"(?:概括|摘要|总结)(?:这|以下|上述|给定|已给|后面|内容|文本|[:：])|"
    r"(?:对|将|把)(?:这|以下|上述|给定|已给|后面).{0,24}(?:概括|摘要|总结)|"
    r"\b(?:translate|rewrite|rephrase|proofread|polish)\b",
    re.IGNORECASE,
)


_RELATIVE_DATE_RE = re.compile(
    r"今天|今日|明天|后天|昨天|本周|下周|这个月|本月|下个月|当前|现在|"
    r"\b(?:today|tomorrow|yesterday|this week|next week|this month|next month|currently|now)\b",
    re.IGNORECASE,
)


_FRESH_EXTERNAL_RE = re.compile(
    r"最新|新闻|开市|收盘|股价|汇率|比分|发布了什么|刚刚发布|公开发布|现任|目前的|"
    r"\b(?:latest|breaking news|most recent|"
    r"current (?:price|score|exchange rate|ceo|president|release|version))\b",
    re.IGNORECASE,
)


_CURRENT_RELEASE_UPDATE_RE = re.compile(
    r"(?:有什么|有何)(?:新|最新)(?:发布|更新|动态)",
    re.IGNORECASE,
)


_EXPLICIT_HISTORICAL_TIME_RE = re.compile(
    r"(?:(?<![0-9])[0-9]{4}\s*年?(?![0-9])|当年|彼时|那时)",
    re.IGNORECASE,
)


_SENTENCE_BOUNDARY_RE = re.compile(r"[。！？!?；;\r\n]+|\.(?=\D|$)")


# 查证/核验/验证/交叉验证与 verify 在中文和英文里首先都是普通及物动词（验证假设、核验
# 身份、交叉验证模型）。字面层是模型无法纠错的抢先短路，命中即返回、语义层再也看不到，
# 所以这些动词只有绑定到外部主张类宾语时才算查证请求；判不出来就返回 None 交给语义层。
# fact-check / cross-check 本身只用于核对外部主张，不需要额外约束。见 issue #30 P1-A。
_EXTERNAL_CLAIM_OBJECT = (
    r"(?:说法|消息|新闻|报道|传闻|爆料|公告|声明|通告|来源|出处|原文|"
    r"真伪|真假|真实性|准确性|可信度|是不是真的|是否属实|属实|"
    r"claim|rumou?r|report|announcement|statement|source)"
)


_AMBIGUOUS_VERIFY_VERB = r"(?:查证|核验|交叉验证|验证|\bverify\b)"


_VERIFY_OBJECT_WINDOW = r"[^。！？!?；;]{0,14}?"


_VERIFIED_SOURCE_RE = re.compile(
    r"官方(?:公告|原文|资料|来源)|一手来源|可靠来源|权威来源|"
    rf"{_AMBIGUOUS_VERIFY_VERB}{_VERIFY_OBJECT_WINDOW}{_EXTERNAL_CLAIM_OBJECT}|"
    rf"{_EXTERNAL_CLAIM_OBJECT}{_VERIFY_OBJECT_WINDOW}{_AMBIGUOUS_VERIFY_VERB}|"
    r"只依据(?:该|这个)页面|"
    r"\b(?:official.{0,32}(?:announcement|source|documentation|release notes)|"
    r"primary source|fact-check|cross-check)\b",
    re.IGNORECASE,
)


_URL_LOCAL_SOURCE_ONLY_RE = re.compile(
    r"只依据(?:该|这个)页面|\b(?:using only|based on) (?:that|this|the) page\b",
    re.IGNORECASE,
)


_URL_RE = re.compile(
    r"https?://[^\s\"'”’」`<>。！)）\]】}—–、]+?"
    r"(?=(?:[>）)\]】}]|[—–、]|"
    r"\.{2,}(?=\s*(?:and\b|then\b|search\b(?!\s*=)|read\b(?!\s*=)|open\b(?!\s*=)))|"
    r"[，,；;：:](?=\s*(?:并|然后|and\b|then\b|搜索(?!\s*=)|检索(?!\s*=)|"
    r"search\b(?!\s*=)|read\b(?!\s*=)|open\b(?!\s*=)))|\s|$))",
    re.IGNORECASE,
)


_URL_READ_ACTION_RE = re.compile(
    r"总结|摘要|读取|阅读|分析|概括|只依据|基于|"
    r"打开|看看|看下|翻译|"
    r"\b(?:summarize|read|analyze|review|open|translate|take a look|using only|based on)\b|"
    r"\b(?:call|use|run|invoke|execute)\s+(?:the\s+)?url_read\b|"
    r"(?:调用|使用|运行|执行)\s*url_read",
    re.IGNORECASE,
)


_POSITIVE_WEB_SEARCH_ACTION_RE = re.compile(
    r"(?:联网|上网|网上).{0,8}(?:查|搜索|检索)|"
    r"(?:并|然后)(?:(?:联网|上网|网上)\s*)?(?:搜索|检索|查找).{1,}|"
    r"(?:请|帮我|要)(?:搜索|检索|查找)\s*.{1,}|"
    r"(?:^|[；;，,。]\s*)(?:搜索|检索|查找)(?!算法|功能|结果|框|引擎|组件|模块).{1,}|"
    r"(?:^|[；;，,。]\s*)查询"
    r"(?=[^，,。；;：:！？!?]{0,64}(?:最新|新闻|公告|动态|资讯|官网|官方|现任|目前的|"
    r"一手来源|可靠来源|权威来源|股价|汇率|比分))|"
    r"(?:^|[;:,.!?：]\s*|\b(?:and|then|please|can you|could you|i want you to)\s+)"
    r"(?:search(?: the web| online)?|look up|find online|"
    r"browse (?:(?:the )?(?:public )?web|the internet|online)|"
    r"(?:call|use|run|invoke|execute)\s+(?:the\s+)?web_search|"
    r"(?:do|perform|run|conduct) (?:a )?(?:(?:quick|brief|targeted) )?(?:web|online) search)\b",
    re.IGNORECASE,
)


_NEGATED_ALL_NETWORK_RE = re.compile(
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再)?\s*"
    r"(?:在|于)\s*(?:本次|此次|当前|目前|这个|该|本轮|这次|本条|这条|本|整个)"
    r"(?:请求|任务|问题|对话|轮次|消息|回答|回复|答复|响应|查询)"
    r"(?:中|里|内|范围内|期间)?\s*"
    r"(?:联网|上网|互联网|使用网络|用网络|接入网络|访问(?:互联网|网络)|连接(?:互联网|网络))|"
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行|使用)?\s*"
    r"(?:联网|上网|互联网|使用网络|用网络|接入网络|访问(?:互联网|网络)|连接(?:互联网|网络))"
    r"(?!\s*(?:搜索|检索|查))|"
    r"(?:在|处于)?不联网(?:的情况下|时)?|"
    r"\b(?:do not|don['’]t|dont|never|without)\s+(?:use|using|access|accessing)\s+"
    r"(?:the\s+)?(?:internet|network)\b|"
    r"\b(?:avoid|refrain\s+from)\s+(?:using|accessing)\s+(?:the\s+)?(?:internet|network)\b|"
    r"\b(?:do not|don['’]t|dont|never)\s+go\s+online\b|"
    r"\bavoid\s+going\s+online\b|"
    r"\b(?:please\s+)?(?:work|answer|respond)\s+(?:(?:entirely|fully|completely)\s+)?offline\b|"
    r"\b(?:please\s+)?(?:stay|remain)\s+(?:(?:entirely|fully|completely)\s+)?offline\b|"
    r"\b(?:please\s+)?keep\s+(?:this|it|the answer|the response)?\s*offline\b|"
    r"\b(?:(?:i['’]d|i would|i) prefer|my preference is) (?:an )?offline (?:answer|response)\b|"
    r"\bwithout (?:going|getting) online\b|"
    r"\bwithout (?:connecting|connect|accessing|access) to (?:the )?(?:internet|network)\b|"
    r"\bwithout (?:the )?(?:web|internet|network)(?:\s+access)?\b|"
    r"\b(?:use|using|answer from|answer with|based on)\s+"
    r"(?:local knowledge|offline knowledge)\s+only\b|"
    r"\b(?:use|using)\s+only\s+(?:local knowledge|offline knowledge)\b|"
    r"\b(?:rely|relying)\s+only\s+on\s+(?:local knowledge|offline knowledge)\b|"
    r"(?:请)?(?:保持|维持)?(?:离线|在离线(?:模式|情况下)?)(?:并|地)?(?:回答|处理|工作)|"
    r"\bno\s+(?:internet|network)(?:\s+access)?\b|"
    r"^(?:in\s+)?offline(?:\s+mode)?\b|^离线(?:模式|情况下|状态下)?",
    re.IGNORECASE,
)


_NEGATED_WEB_SEARCH_RE = re.compile(
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行|使用)?\s*"
    r"(?:联网|上网|网上)\s*(?:搜索|检索|查询|查找|查)|"
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行|使用)?\s*"
    r"(?:搜索|检索|查找|查(?!询|找))|"
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行|使用)?\s*"
    r"查询|"
    r"\b(?:do not|don['’]t|dont|never|without)\s+"
    r"(?:search(?:ing)?(?: the)? web|look(?:ing)? up|find(?:ing)? online|"
    r"brows(?:e|ing)(?: the)? web)\b|"
    r"\b(?:do not|don['’]t|dont|never)\s+search\b|"
    r"\b(?:do not|don['’]t|dont|never)\s+look\s+(?:it|this|that|them)\s+up\b|"
    r"\b(?:avoid|refrain\s+from|skip)\s+(?:searching|search|browsing|brows(?:e|ing))\s+"
    r"(?:the\s+)?(?:web|internet|online)\b|"
    r"\bno\s+(?:web|online|internet)\s+(?:search|lookup)\b",
    re.IGNORECASE,
)


_NEGATED_URL_READ_RE = re.compile(
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行|使用)?\s*"
    r"(?:打开|读取|阅读|访问|浏览)(?:网页|网站|页面|链接|url)?|"
    r"\b(?:do not|don['’]t|dont|never|without)\s+"
    r"(?:open(?:ing)?|read(?:ing)?|access(?:ing)?|brows(?:e|ing))\b|"
    r"\b(?:avoid|refrain\s+from|skip)\s+"
    r"(?:opening|reading|accessing|browsing)\b",
    re.IGNORECASE,
)


_NEGATED_VERIFIED_WEB_RE = re.compile(
    r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可)(?:再|去|进行)?\s*"
    r"(?:查证|核验|验证|交叉验证)[^，,。；;]*|"
    r"\b(?:do not|don['’]t|dont|never|without)\s+"
    r"(?:verify|verifying|fact[- ]check(?:ing)?|cross[- ]check(?:ing)?)\b[^,.;!?]*|"
    r"\b(?:avoid|refrain\s+from|skip)\s+"
    r"(?:verifying|fact[- ]checking|cross[- ]checking)\b[^,.;!?]*|"
    r"\b(?:do not|don['’]t|dont|never)\s+consult\s+(?:the\s+)?"
    r"(?:official|primary|authoritative)\s+sources?\b[^,.;!?]*|"
    r"\b(?:do not|don['’]t|dont|never)\s+(?:use|check)\s+(?:the\s+)?"
    r"(?:official|primary|authoritative)\s+sources?\b[^,.;!?]*|"
    r"\b(?:avoid|refrain\s+from|skip)\s+(?:checking|using)\s+(?:the\s+)?"
    r"(?:official|primary|authoritative)\s+sources?\b[^,.;!?]*|"
    r"\b(?:use|check|consult)\s+no\s+(?:official|primary|authoritative)\s+sources?\b[^,.;!?]*|"
    r"\b(?:exclude|excluding|omit|omitting|skip|skipping)\s+(?:the\s+)?"
    r"(?:official|primary|authoritative)\s+sources?\b[^,.;!?]*",
    re.IGNORECASE,
)


_IN_DOCUMENT_SEARCH_RE = re.compile(
    r"\bsearch\s+(?:within|inside)\s+(?:the\s+)?(?:page|document)\b|"
    r"\bsearch\s+(?:within|inside)\s+(?:this|that|it)\b|"
    r"\bsearch\s+(?:this|that|the)\s+(?:page|document)\b|"
    r"\bsearch\s+(?:it|its\s+contents?)\s+for\b|"
    r"\bsearch\s+(?:the\s+)?(?:page|document)\s+for\b|"
    r"(?:搜索|检索|查找)(?:该|这个|此)?(?:页面|文档)(?:中|内|里|中的|里的)",
    re.IGNORECASE,
)


_NEGATED_WEB_TOOL_NAME_RE = re.compile(
    r"(?:不要|不用|别|请勿|禁止|严禁|不得|不可).{0,16}?\bweb_search\b|"
    r"\b(?:do not|don['’]t|dont|never)\s+(?:call|use|invoke|run|execute)\s+"
    r"(?:the\s+)?(?:tool\s+)?web_search\b(?:\s+tool\b)?|"
    r"\b(?:without|avoid(?:ing)?|refrain\s+from|skip(?:ping)?)\s+"
    r"(?:call(?:ing)?|us(?:e|ing)|invok(?:e|ing)|run(?:ning)?|execut(?:e|ing))\s+"
    r"(?:the\s+)?(?:tool\s+)?web_search\b(?:\s+tool\b)?",
    re.IGNORECASE,
)


_NEGATED_URL_TOOL_NAME_RE = re.compile(
    r"(?:不要|不用|别|请勿|禁止|严禁|不得|不可).{0,16}?\burl_read\b|"
    r"\b(?:do not|don['’]t|dont|never)\s+(?:call|use|invoke|run|execute)\s+"
    r"(?:the\s+)?(?:tool\s+)?url_read\b(?:\s+tool\b)?|"
    r"\b(?:without|avoid(?:ing)?|refrain\s+from|skip(?:ping)?)\s+"
    r"(?:call(?:ing)?|us(?:e|ing)|invok(?:e|ing)|run(?:ning)?|execut(?:e|ing))\s+"
    r"(?:the\s+)?(?:tool\s+)?url_read\b(?:\s+tool\b)?",
    re.IGNORECASE,
)


_POSITIVE_WEB_TOOL_NAME_RE = re.compile(
    r"(?:调用|使用|运行|执行)\s*web_search\b|"
    r"\b(?:call|use|invoke|run|execute)\s+(?:the\s+)?web_search\b",
    re.IGNORECASE,
)


_POSITIVE_URL_TOOL_NAME_RE = re.compile(
    r"(?:调用|使用|运行|执行)\s*url_read\b|"
    r"\b(?:call|use|invoke|run|execute)\s+(?:the\s+)?url_read\b",
    re.IGNORECASE,
)


_QUOTED_LITERAL_RE = re.compile(
    r"(?:“[^”]{1,240}”|‘[^’]{1,240}’|\"[^\"]{1,240}\"|(?<!\w)'[^']{1,240}'(?!\w)|"
    r"「[^」]{1,240}」|`[^`]{1,240}`)"
)


_QUOTED_RESOURCE_RE = re.compile(
    r"https?://[^\s\"'”’」`]+|\bmcp_[A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _RequestSignals:
    """一次请求的字面信号；只从用户消息推导，不含上下文与工具授权。"""

    message: str
    control_message: str
    routing_message: str
    web_search_denied: bool
    url_read_denied: bool
    all_network_denied: bool
    include_current_date: bool
    original_transform_request: bool
    explicit_web_search_request: bool
    url_read_request: bool
    verified_web_request: bool
    independent_verified_web_request: bool
    fresh_web_request: bool


def _extract_request_signals(message: str) -> _RequestSignals:
    original_transform_request = bool(_TRANSFORM_RE.search(message))
    control_message = _mask_quoted_literals(message)
    routing_message, web_search_denied, url_read_denied, all_network_denied = _resolve_network_scope(control_message)
    external_signal_message = _URL_RE.sub(" ", routing_message)
    external_web_action_message = _IN_DOCUMENT_SEARCH_RE.sub(" ", external_signal_message)
    include_current_date = _needs_current_date(routing_message)
    explicit_web_search_request = bool(
        _POSITIVE_WEB_SEARCH_ACTION_RE.search(external_web_action_message)
        or _POSITIVE_WEB_TOOL_NAME_RE.search(external_web_action_message)
    )
    url_read_request = bool(_URL_RE.search(routing_message) and _URL_READ_ACTION_RE.search(routing_message))
    verified_web_request = bool(_VERIFIED_SOURCE_RE.search(external_signal_message))
    independent_verified_web_request = verified_web_request and not _URL_LOCAL_SOURCE_ONLY_RE.search(
        external_signal_message
    )
    fresh_web_request = bool(
        _FRESH_EXTERNAL_RE.search(external_signal_message)
        or _has_current_release_update_request(external_signal_message)
    )
    return _RequestSignals(
        message=message,
        control_message=control_message,
        routing_message=routing_message,
        web_search_denied=web_search_denied,
        url_read_denied=url_read_denied,
        all_network_denied=all_network_denied,
        include_current_date=include_current_date,
        original_transform_request=original_transform_request,
        explicit_web_search_request=explicit_web_search_request,
        url_read_request=url_read_request,
        verified_web_request=verified_web_request,
        independent_verified_web_request=independent_verified_web_request,
        fresh_web_request=fresh_web_request,
    )


def _mask_quoted_literals(message: str) -> str:
    def mask_match(match: re.Match[str]) -> str:
        masked = [" "] * len(match.group(0))
        for resource_match in _QUOTED_RESOURCE_RE.finditer(match.group(0)):
            masked[resource_match.start() : resource_match.end()] = resource_match.group(0)
        return "".join(masked)

    return _QUOTED_LITERAL_RE.sub(mask_match, message)


def _resolve_network_scope(message: str) -> tuple[str, bool, bool, bool]:
    network_denial_patterns = (
        _NEGATED_ALL_NETWORK_RE,
        _NEGATED_WEB_SEARCH_RE,
        _NEGATED_URL_READ_RE,
        _NEGATED_VERIFIED_WEB_RE,
        _NEGATED_WEB_TOOL_NAME_RE,
        _NEGATED_URL_TOOL_NAME_RE,
    )
    if not any(pattern.search(message) for pattern in network_denial_patterns):
        return message, False, False, False

    clauses = re.split(
        r"(?:[，,。；;]|[.!:：](?=\s)|\s+[—–]\s+|[—–]{2}|但(?:是)?|不过|而(?:是|要)?|\bbut\b|"
        r"\band\s+(?=(?:do not|don['’]t|dont|never|search|read|open|find|verify|"
        r"cross-check|call|use|browse|summarize|analyze|translate|do\s+a|perform)\b)|"
        r"\b(?:then|afterwards|later|finally)\s+(?=(?:search|read|open|find|verify|"
        r"cross-check|call|use|browse|summarize|analyze|translate|do\s+a|perform)\b)|"
        r"(?:并且|并|然后|随后|最后)(?=(?:不要|请勿|禁止|严禁|不得|不可|搜索|检索|查询|读取|打开|查找|核验|验证|调用|使用|"
        r"总结|摘要|分析|翻译)))\s*",
        message,
        flags=re.IGNORECASE,
    )
    web_search_denied = False
    url_read_denied = False
    all_network_denied = False
    hard_current_network_denied = any(
        _is_current_request_network_denial(message, match) for match in _NEGATED_ALL_NETWORK_RE.finditer(message)
    )
    hard_web_tool_denied = False
    hard_url_tool_denied = False
    routing_clauses: list[str] = []
    allowed_web_objects: set[str] = set()
    allowed_url_targets: set[str] = set()

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        negative_matches = [(match.start(), match.end(), "all") for match in _NEGATED_ALL_NETWORK_RE.finditer(clause)]
        negative_matches.extend(
            (match.start(), match.end(), "web") for match in _NEGATED_WEB_SEARCH_RE.finditer(clause)
        )
        negative_matches.extend((match.start(), match.end(), "url") for match in _NEGATED_URL_READ_RE.finditer(clause))
        negative_matches.extend(
            (match.start(), match.end(), "web") for match in _NEGATED_VERIFIED_WEB_RE.finditer(clause)
        )
        explicit_tool_events: list[tuple[int, str, bool, bool]] = []
        for match in _NEGATED_WEB_TOOL_NAME_RE.finditer(clause):
            negative_matches.append((match.start(), match.end(), "web"))
            explicit_tool_events.append((match.start(), "web", False, _has_scoped_denial_tail(clause, match.end())))
        for match in _NEGATED_URL_TOOL_NAME_RE.finditer(clause):
            negative_matches.append((match.start(), match.end(), "url"))
            explicit_tool_events.append((match.start(), "url", False, _has_scoped_denial_tail(clause, match.end())))
        explicit_negative_spans = [(start, end) for start, end, _capability in negative_matches]
        explicit_tool_events.extend(
            (match.start(), "web", True, False)
            for match in _POSITIVE_WEB_TOOL_NAME_RE.finditer(clause)
            if not _overlaps_any(match.span(), explicit_negative_spans)
        )
        explicit_tool_events.extend(
            (match.start(), "url", True, False)
            for match in _POSITIVE_URL_TOOL_NAME_RE.finditer(clause)
            if not _overlaps_any(match.span(), explicit_negative_spans)
        )
        routing_negative_matches = tuple(negative_matches)
        negative_web_object = _extract_web_request_object(clause)
        negative_url_targets = _extract_url_targets(clause)
        if (
            negative_web_object
            and allowed_web_objects
            and not _is_anaphoric_request_object(negative_web_object)
            and any(existing != negative_web_object for existing in allowed_web_objects)
        ):
            negative_matches = [match for match in negative_matches if match[2] != "web"]
        if negative_url_targets and allowed_url_targets and not negative_url_targets.issuperset(allowed_url_targets):
            negative_matches = [match for match in negative_matches if match[2] != "url"]
        events: list[tuple[int, str, bool]] = []
        for start, _end, capability in negative_matches:
            if capability == "all":
                if _is_current_request_scope_tail(clause, _end):
                    hard_current_network_denied = True
                if _has_scoped_denial_tail(clause, _end):
                    continue
                events.extend(((start, "web", False), (start, "url", False)))
                all_network_denied = True
            else:
                events.append((start, capability, False))

        events.extend(
            (position, capability, True) for position, capability, allowed, _scoped in explicit_tool_events if allowed
        )

        negative_spans = [(start, end) for start, end, _ in routing_negative_matches]
        for match in _POSITIVE_WEB_SEARCH_ACTION_RE.finditer(clause):
            if not _overlaps_any(match.span(), negative_spans):
                events.append((match.start(), "web", True))
                web_object = _extract_web_request_object(clause)
                if web_object:
                    allowed_web_objects.add(web_object)
        if _URL_RE.search(clause):
            for match in _URL_READ_ACTION_RE.finditer(clause):
                if not _overlaps_any(match.span(), negative_spans):
                    events.append((match.start(), "url", True))
                    allowed_url_targets.update(_extract_url_targets(clause))
        for match in _VERIFIED_SOURCE_RE.finditer(_URL_RE.sub(" ", clause)):
            if not negative_matches and not _overlaps_any(match.span(), negative_spans):
                events.extend(((match.start(), "web", True), (match.start(), "url", True)))

        for _position, capability, allowed in sorted(events, key=lambda event: event[0]):
            if capability == "web":
                web_search_denied = not allowed
            else:
                url_read_denied = not allowed
            if allowed and not hard_current_network_denied:
                all_network_denied = False

        for _position, capability, allowed, scoped in sorted(explicit_tool_events, key=lambda event: event[0]):
            if scoped:
                continue
            if capability == "web":
                hard_web_tool_denied = not allowed
            else:
                hard_url_tool_denied = not allowed

        if not routing_negative_matches or any(allowed for _, _, allowed in events):
            routing_clauses.append(clause)

    return (
        "; ".join(routing_clauses),
        web_search_denied or hard_current_network_denied or hard_web_tool_denied,
        url_read_denied or hard_current_network_denied or hard_url_tool_denied,
        all_network_denied,
    )


def _has_scoped_denial_tail(clause: str, match_end: int) -> bool:
    return _extract_directive_scope(clause, match_end) is not None


def _is_current_request_network_denial(message: str, match: re.Match[str]) -> bool:
    if _extract_directive_scope(message, match.end()) is not None:
        return False
    matched_text = match.group(0)
    has_embedded_current_scope = bool(
        re.search(
            r"(?:本次|此次|当前|目前|这个|该|本轮|这次|本条|这条|本|整个)"
            r"(?:请求|任务|问题|对话|轮次|消息|回答|回复|答复|响应|查询)"
            r"(?:中|里|内|范围内|期间)?",
            matched_text,
        )
    )
    return bool(
        has_embedded_current_scope
        or _has_current_request_scope_prefix(message, match.start())
        or _is_current_request_scope_tail(message, match.end())
    )


def _has_current_request_scope_prefix(message: str, match_start: int) -> bool:
    prefix = message[:match_start]
    return bool(
        re.search(
            r"(?:\b(?:for|in|within|during|regarding|on)\s+"
            r"(?:(?:this|my|your|the)\s+)?"
            r"(?:(?:current|present|specific|entire|whole|full)\s+)?"
            r"(?:request|task|question|conversation|chat|turn|message|answer|reply|response|query)|"
            r"(?:针对|关于|在|就)?(?:本次|此次|当前|目前|这个|该|本轮|这次|本条|这条|本|整个)"
            r"(?:请求|任务|问题|对话|轮次|消息|回答|回复|答复|响应|查询)"
            r"(?:中|里|内|范围内|期间|而言)?)"
            r"\s*[,，:：]?\s*(?:(?:please|kindly)\s+|(?:请|麻烦)?\s*务必\s*|(?:请|麻烦)\s*)?$",
            prefix,
            re.IGNORECASE,
        )
    )


def _is_current_request_scope_tail(clause: str, match_end: int) -> bool:
    tail = clause[match_end:]
    return bool(
        re.match(
            r"\s*(?:for|about|in|within|during|regarding|on)\b\s+"
            r"(?:this(?:\s+one)?|"
            r"(?:(?:this|the|my|your|any)\s+)?"
            r"(?:(?:very|current|present|specific|entire|whole|full)\s+)?"
            r"(?:request|task|question|conversation|chat|turn|message|answer|reply|response|query)"
            r"(?:\s+at\s+hand)?)\b|"
            r"\s*(?:用于|针对|关于|在|于)(?:本次|此次|当前|目前|这个|该|本轮|这次|本条|这条|本|整个)"
            r"(?:请求|任务|问题|对话|轮次|消息|回答|回复|答复|响应|查询)"
            r"(?:中|里|内|范围内|期间)?",
            tail,
            re.IGNORECASE,
        )
    )


def _extract_directive_scope(clause: str, match_end: int) -> str | None:
    tail = clause[match_end:]
    scoped_match = re.match(
        r"\s*(?:for|about|in|within|during|regarding|on)\b\s+"
        r"(?P<english>[^\s,.;!?，。；：！？]+(?:\s+[^\s,.;!?，。；：！？]+){0,3})|"
        r"\s*(?:用于|针对|关于)(?P<chinese>[^,.;!?，。；：！？]+)",
        tail,
        re.IGNORECASE,
    )
    if scoped_match is None:
        return None
    scope = (scoped_match.group("english") or scoped_match.group("chinese") or "").strip(".,;:!?，。；：！？")
    if _is_current_request_scope_tail(clause, match_end):
        return None
    return scope.lower()


def _extract_url_targets(clause: str) -> set[str]:
    return {match.group(0).rstrip(".,;:!?，。；：！？") for match in _URL_RE.finditer(clause)}


def _extract_web_request_object(clause: str) -> str:
    normalized = clause.lower()
    normalized = re.sub(r"\b(?:newest|most\s+recent)\b", "latest", normalized)
    normalized = re.sub(r"\b(?:do not|don['’]t|dont|without|never)\b", " ", normalized)
    normalized = re.sub(
        r"\b(?:call|use|invoke|run)\s+(?:the\s+)?(?:tool\s+)?"
        r"(?:web_search|url_read)\b(?:\s+tool\b)?",
        " ",
        normalized,
    )
    normalized = re.sub(r"(?:调用|使用|运行|执行)\s*(?:web_search|url_read)", " ", normalized)
    normalized = re.sub(
        r"(?:不要|不用|无需|不需要|不必|别|请勿|禁止|严禁|不得|不可|请|帮我|要|再|随后)",
        " ",
        normalized,
    )
    normalized = re.sub(
        r"\b(?:search(?:ing)?(?: the)? web(?: for)?|search|look(?:ing)? up|find(?:ing)? online|"
        r"brows(?:e|ing)(?: the)? web|look\s+(?:it|this|that|them)\s+up)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"(?:联网|上网|网上)?(?:搜索|检索|查询|查找|查)", " ", normalized)
    normalized = re.sub(r"(?:用于|针对|关于)", " ", normalized)
    normalized = re.sub(r"\b(?:the|a|an|for|about|regarding|on|please)\b", " ", normalized)
    normalized = re.sub(r"\b(?:again|anymore|any longer|further)\b", " ", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_anaphoric_request_object(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:it|this|that|them|this topic|that topic|this subject|that subject|"
            r"this content|that content|the above content|above content|the same topic|same topic|"
            r"它|这个|那个|这些|那些|该主题|这个主题|那个主题|同一主题|相同主题|"
            r"这个话题|那个话题|这个内容|那个内容|上述内容|以上内容|该内容)",
            value,
            re.IGNORECASE,
        )
    )


def _overlaps_any(span: tuple[int, int], other_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in other_spans)


def _has_current_release_update_request(message: str) -> bool:
    for sentence in _SENTENCE_BOUNDARY_RE.split(message):
        if _EXPLICIT_HISTORICAL_TIME_RE.search(sentence):
            continue
        if _RELATIVE_DATE_RE.search(sentence) and _CURRENT_RELEASE_UPDATE_RE.search(sentence):
            return True
    return False


def _needs_current_date(message: str) -> bool:
    return bool(_RELATIVE_DATE_RE.search(message))
