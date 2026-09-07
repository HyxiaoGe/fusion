"""在规范 reader Markdown 包装中定位标题精确匹配的文章正文。"""

import html
import re
import unicodedata

_MARKDOWN_MARKER = re.compile(r"(?im)^Markdown Content:[ \t]*\r?$")
_SOURCE_HEADER = re.compile(r"(?im)^URL Source:[ \t]*https?://")
_H1 = re.compile(r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def select_article_body(content: str, title: str) -> str:
    """只有规范包装及精确 H1 标题均成立才跳过导航，否则原样保留。"""

    expected_title = _normalize_title(title)
    marker = _MARKDOWN_MARKER.search(content[:4000])
    if not expected_title or marker is None:
        return content
    header = content[: marker.start()]
    if not header.lstrip().startswith("Title:") or not _SOURCE_HEADER.search(header):
        return content
    offset = marker.end()
    active_fence = ""
    for line in content[offset:].splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fence = _FENCE.match(stripped)
        if fence:
            token, suffix = fence.groups()
            if not active_fence:
                active_fence = token
            elif token[0] == active_fence[0] and len(token) >= len(active_fence) and not suffix.strip():
                active_fence = ""
        elif not active_fence:
            heading = _H1.match(stripped)
            if heading and _normalize_title(heading.group(1)) == expected_title:
                return content[offset:]
        offset += len(line)
    return content


def _normalize_title(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", html.unescape(str(title or ""))).split()).casefold()
