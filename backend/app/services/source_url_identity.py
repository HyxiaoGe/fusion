"""网页来源统一身份键；结果仅用于比较，不能覆盖显示或读取的原始 URL。"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 沿用搜索候选已有的跟踪字段集合；其余业务参数原样参与身份比较。
TRACKING_QUERY_PARAMS = {
    "_hsenc",
    "_hsmi",
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "msclkid",
    "spm",
    "ttclid",
    "twclid",
    "vero_conv",
    "vero_id",
    "yclid",
}


def canonicalize_source_url(url: str) -> str:
    """统一搜索、引用、研究读页和 evidence 的身份规则。"""
    stripped = (url or "").strip()
    try:
        parsed = urlsplit(stripped)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return ""
    if scheme not in {"http", "https"} or not host:
        return ""
    while host.startswith("www."):
        host = host[4:]
    netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_PARAMS
    ]
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), urlencode(sorted(query_items)), ""))
