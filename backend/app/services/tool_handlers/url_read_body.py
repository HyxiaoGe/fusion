"""在 reader Markdown 中定位正文窗口，保留无法确认是导航的前缀。"""

import html
import re
import unicodedata
from urllib.parse import urlsplit

_MARKDOWN_MARKER = re.compile(r"(?im)^Markdown Content:[ \t]*\r?$")
_SOURCE_HEADER = re.compile(r"(?im)^URL Source:[ \t]*https?://")
_H1 = re.compile(r"^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_IMAGE = re.compile(r"!\[(?:\\.|[^\[\]\\])*\]\((?:<[^>\r\n]+>|[^()\r\n]+)\)")
_LINK = re.compile(r"\[(?:\\.|[^\[\]\\])*\]\((<[^>\r\n]+>|[^\s()]+)(?:[ \t]+(?:\"[^\"\r\n]*\"|'[^'\r\n]*'))?[ \t]*\)")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
_NAV_REMAINDER = re.compile(r"[ \t|·›»>]*")
_SEPARATOR = re.compile(r"(?:[-*_][ \t]*){3,}")
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+")


def select_article_body(content: str, title: str, *, url: str = "") -> str:
    """精确 H1 前只跳过规范包装或可确认的站内链接导航。"""

    expected_title = _normalize_title(title)
    marker = _MARKDOWN_MARKER.search(content[:4000])
    if not expected_title:
        return content
    header = content[: marker.start()] if marker else ""
    wrapped = bool(marker and header.lstrip().startswith("Title:") and _SOURCE_HEADER.search(header))
    offset = marker.end() if wrapped else 0
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
                if wrapped or (
                    _is_navigation_prefix(content[:offset], url) and _has_article_text(content[offset + len(line) :])
                ):
                    return content[offset:]
                # 此 H1 已使后续候选的前缀包含非导航正文，无需再重复扫描前缀。
                return content
        offset += len(line)
    return content


def _is_navigation_prefix(prefix: str, url: str) -> bool:
    """仅接受站内链接、标志图片和列表分隔符，目录锚点或任何正文均保留。"""

    try:
        source_host = _normalize_host(urlsplit(url).hostname or "")
    except ValueError:
        return False
    if not source_host:
        return False
    has_link = False
    for line in prefix.splitlines():
        if line.startswith(("    ", "\t")):
            return False
        stripped = line.strip()
        if not stripped or _SEPARATOR.fullmatch(stripped):
            continue
        # 先去图片，才能处理 [![标志](图片地址)](站内地址) 这种嵌套链接。
        without_images = _IMAGE.sub("图片", line)
        for match in _LINK.finditer(without_images):
            target = html.unescape(match.group(1).strip("<>"))
            try:
                parsed = urlsplit(target)
                target_host = _normalize_host(parsed.hostname or "")
            except ValueError:
                return False
            if (
                not target
                or parsed.fragment
                or target.startswith("#")
                or parsed.scheme not in {"", "http", "https"}
                or (target_host and target_host != source_host)
                or (parsed.scheme and not target_host)
            ):
                return False
            has_link = True
        remainder = _LIST_MARKER.sub("", _LINK.sub("", without_images))
        if not _NAV_REMAINDER.fullmatch(remainder):
            return False
    return has_link


def _has_article_text(body: str) -> bool:
    """标题后的同一节须包含正文，避免把纯链接目录误作文章。"""

    for line in body.splitlines():
        if _H1.match(line):
            break
        if _HEADING.match(line):
            continue
        remainder = _LIST_MARKER.sub("", _LINK.sub("", _IMAGE.sub("", line)))
        if any(character.isalnum() for character in remainder):
            return True
    return False


def _normalize_host(host: str) -> str:
    return host.lower().removeprefix("www.").rstrip(".")


def _normalize_title(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", html.unescape(str(title or ""))).split()).casefold()
