"""
url_read：经 agent-tools 网关调用读取服务的 read_page。

网址检查（格式、私网、带凭据的参数）、几种读取方式的先后与总时限、去掉读取包装、正文从标题处开始、8000 字截断、
没读到正文或被网站拦下的判定、给模型的说明和失败话术都在服务里（agent-tools services/reader）。
这里只做 Fusion 自己的事：
- 工具在 Fusion 叫 url_read；说明用服务原文，后面接 Fusion 的读取约定（url-read-tool-description 模板）。
  服务目录还没读到时用同名的最小定义（只有 url），调用照常走网关。
- 每轮次数上限（network_budget）。
- 结果编成全局引用编号 [n] 的不可信来源上下文；读取卡片和站点图标（服务放在 meta.favicon）。
- 日志和事件里的网址按 url_policy 脱敏。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from app.ai.prompts.agent_loop import get_url_read_tool_description
from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import SourceReference, UrlBlock
from app.services.agent.sanitizer import sanitize_url_read_arguments
from app.services.external.agent_tools_mcp import (
    GatewayToolCatalog,
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
    gateway_tool_catalog,
)
from app.services.mcp.tool_contract import sanitize_tool_schema_for_model, validate_tool_arguments
from app.services.security.url_policy import evaluate_url_policy
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

URL_READ_TOOL_NAME = "url_read"
READER_SERVICE = "reader"
READER_REMOTE_TOOL = "read_page"
_CALL_SCENE = "url_read"
# 服务整次读取 10 秒时限（几种读取方式一起算），留出网关转发的余量；执行器单次工具 30 秒。
_CALL_TIMEOUT_SECONDS = 15.0
# 网关 429 是本产品当天的调用额度用完了：再读别的网页也一样，按额度用完告诉模型。
_GATEWAY_LIMIT_CODE = "rate_limited"
QUOTA_EXHAUSTED_CODE = "quota_exhausted"
_MINIMAL_PARAMETERS = {
    "type": "object",
    "properties": {"url": {"type": "string"}},
    "required": ["url"],
    "additionalProperties": False,
}
# 读取卡片上给用户看的失败说明；给模型的说明用服务原文。
# 前端 toolErrorDisplay 只展示以「，已跳过该来源」结尾的固定说明，其余显示「未能读取」
_USER_FAILURE_MESSAGES = {
    "invalid_url": "链接格式无效，已跳过该来源",
    "unsafe_url": "该链接不允许读取，已跳过该来源",
    "page_not_found": "网页不存在（链接已失效或地址有误），已跳过该来源",
    "page_error": "网页返回错误页，未取得正文，已跳过该来源",
    "access_blocked": "网页访问受限，未取得正文，已跳过该来源",
    "empty_content": "网页读取未取得正文，已跳过该来源",
    "timeout": "网页读取超时，已跳过该来源",
    "rate_limited": "网页读取请求暂时受限，已跳过该来源",
    QUOTA_EXHAUSTED_CODE: "今日网页读取额度已用完，已跳过该来源",
}
_DEFAULT_USER_FAILURE_MESSAGE = "网页暂时无法读取，已跳过该来源"


@dataclass(frozen=True)
class PageText:
    """读到的一页：服务已经去掉包装、截好窗口；note 是服务写给模型的截断/正文提取说明。"""

    url: str
    title: str
    content: str
    favicon: str | None = None
    note: str | None = None


def build_url_read_tool(catalog: GatewayToolCatalog | None = None) -> dict:
    """每个 Run 装配一次：服务说明 + Fusion 的读取约定。"""

    product_notes = get_url_read_tool_description()
    spec = _reader_spec(catalog)
    if spec is not None:
        return build_gateway_tool_definition(spec, name=URL_READ_TOOL_NAME, product_notes=product_notes)
    return {
        "type": "function",
        "function": {
            "name": URL_READ_TOOL_NAME,
            "description": f"{render_runtime_prompt('ai_tools.url_read_minimal_description')}\n\n{product_notes}",
            "parameters": copy.deepcopy(_MINIMAL_PARAMETERS),
        },
    }


async def read_page(url: str, *, scene: str, full_page: bool = False) -> GatewayToolOutcome:
    arguments: dict[str, Any] = {"url": url}
    if full_page:
        arguments["full_page"] = True
    return await _call_read_page(arguments, scene=scene)


def page_from_outcome(outcome: GatewayToolOutcome, *, requested_url: str = "") -> PageText | None:
    """读到了正文才算一页；服务失败、或成功却没有正文（不该发生）都返回 None。"""
    if not outcome.ok:
        return None
    page = _page(outcome.data, requested_url)
    return page if page.content.strip() else None


def _page(data: dict[str, Any], requested_url: str) -> PageText:
    meta = _meta(data)
    favicon = meta.get("favicon")
    note = data.get("note")
    return PageText(
        url=_text(data.get("url")) or requested_url,
        title=_text(data.get("title")),
        content=_text(data.get("content")),
        favicon=favicon if isinstance(favicon, str) and favicon else None,
        note=note.strip() if isinstance(note, str) and note.strip() else None,
    )


def _reader_spec(catalog: GatewayToolCatalog | None = None) -> GatewayToolSpec | None:
    return (catalog or gateway_tool_catalog()).spec(READER_SERVICE, READER_REMOTE_TOOL)


def _announced_parameters() -> dict[str, Any]:
    spec = _reader_spec()
    if spec is None:
        return copy.deepcopy(_MINIMAL_PARAMETERS)
    return sanitize_tool_schema_for_model(spec.input_schema, keep_descriptions=True)


async def _call_read_page(arguments: dict[str, Any], *, scene: str) -> GatewayToolOutcome:
    return await call_gateway_tool(
        READER_SERVICE,
        READER_REMOTE_TOOL,
        arguments,
        scene=scene,
        min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
    )


class UrlReadHandler(BaseToolHandler):
    supports_run_level_citations = True
    # 服务里几种读取方式已经各试过；失败后这个网址还值不值得再读由服务的说明告诉模型，这里不自动重试。
    supports_automatic_retry = False

    @property
    def tool_name(self) -> str:
        return URL_READ_TOOL_NAME

    @property
    def sse_event_prefix(self) -> str:
        return "url_read"

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        return sanitize_url_read_arguments(input_params)

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        data = result.data if isinstance(result.data, dict) else {}
        safe_output: dict[str, Any] = {}
        if isinstance(data.get("url"), str):
            safe_output["url"] = _safe_log_url(data["url"])
        for key in ("failure_kind", "provider"):
            value = data.get(key)
            if isinstance(value, str):
                safe_output[key] = value[:80]
        for key in ("retryable", "budget_limited", "truncated", "extracted"):
            value = data.get(key)
            if isinstance(value, bool):
                safe_output[key] = value
        for key in ("upstream_status", "attempts", "content_length", "reader_fetch_ms"):
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_output[key] = value
        return safe_output

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """只按公告的 schema 校验形状；网址能不能读由服务检查并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(_announced_parameters(), args)

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        requested_url = args.get("url") if isinstance(args.get("url"), str) else ""
        safe_url = _safe_log_url(requested_url)
        start = time.monotonic()
        outcome = await _call_read_page(dict(args), scene=_CALL_SCENE)
        duration_ms = int((time.monotonic() - start) * 1000)
        if not outcome.ok:
            error_code = outcome.error_code or "tool_error"
            if not outcome.model_text:
                logger.warning("读取服务没有答复: error_code=%s url=%s", error_code, safe_url)
                if error_code == _GATEWAY_LIMIT_CODE:
                    error_code = QUOTA_EXHAUSTED_CODE
            status = outcome.error.get("status")
            return ToolResult(
                status="degraded",
                duration_ms=duration_ms,
                error_message=_USER_FAILURE_MESSAGES.get(error_code, _DEFAULT_USER_FAILURE_MESSAGE),
                data={
                    "url": safe_url,
                    "failure_kind": error_code,
                    "retryable": outcome.error.get("retryable") is True,
                    "upstream_status": status if isinstance(status, int) and not isinstance(status, bool) else None,
                    "model_text": outcome.model_text,
                },
            )

        page = _page(outcome.data, requested_url)
        if not page.content.strip():
            logger.warning("读取服务返回成功但没有正文: url=%s", safe_url)
            return ToolResult(
                status="degraded",
                duration_ms=duration_ms,
                error_message=_USER_FAILURE_MESSAGES["empty_content"],
                data={"url": safe_url, "failure_kind": "empty_content", "retryable": False, "model_text": ""},
            )
        meta = _meta(outcome.data)
        content_chars = outcome.data.get("content_chars")
        return ToolResult(
            status="success",
            duration_ms=duration_ms,
            data={
                "url": page.url,
                "title": page.title,
                "content": page.content,
                "favicon": page.favicon,
                "note": page.note,
                "truncated": outcome.data.get("truncated") is True,
                "extracted": outcome.data.get("extracted") is True,
                "content_length": content_chars if isinstance(content_chars, int) else len(page.content),
                "provider": meta.get("provider") if isinstance(meta.get("provider"), str) else None,
                "attempts": meta.get("attempts"),
                "reader_fetch_ms": meta.get("fetch_ms"),
            },
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> UrlBlock:
        url = result.data.get("url", "")
        source_refs = []
        if result.status == "success" and url:
            source_refs.append(
                SourceReference(
                    kind="url_read",
                    title=result.data.get("title") or "",
                    url=url,
                    favicon=result.data.get("favicon"),
                    status=result.status,
                    tool_call_log_id=log_id,
                )
            )
        return UrlBlock(
            type="url_read",
            id=block_id,
            url=url,
            title=result.data.get("title"),
            favicon=result.data.get("favicon"),
            tool_call_log_id=log_id,
            status=result.status,
            error_message=result.error_message,
            source_count=len(source_refs),
            source_refs=source_refs,
        )

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        url = result.data.get("url", "")
        content = result.data.get("content", "")
        request_context = render_runtime_prompt("tool_handlers.url_read_request", url=url)

        if result.data.get("budget_limited"):
            return f"{request_context}\n{render_runtime_prompt('tool_handlers.url_read_budget_exhausted')}"
        if result.status != "success" or not content:
            return f"{request_context}\n{_failure_text(result.data)}"

        # 编号由运行期注册表分配，并与持久化 source_refs 共享，不能按读页次数重置。
        citation_number = citation_numbers[0] if citation_numbers else None
        if not isinstance(citation_number, int) or isinstance(citation_number, bool) or citation_number < 1:
            citation_number = None
        context = format_untrusted_source_context(
            UntrustedSourceContext(
                source_id=str(citation_number) if citation_number is not None else "url-read-unassigned",
                source_type="url_read",
                title=result.data.get("title") or "Unknown",
                url=url,
                content=content,
                provider="web",
            ),
            # 服务已经截好窗口（截断时 note 里写明），这里不再截。
            max_chars=len(content),
        )
        parts = [request_context, f"[{citation_number}]\n{context}" if citation_number is not None else context]
        if result.data.get("note"):
            parts.append(render_runtime_prompt("tool_handlers.url_read_note", note=result.data["note"]))
        return "\n".join(parts)

    def _build_result_summary(self, result: ToolResult) -> dict:
        """URL 读取轻量摘要：title + favicon。

        不返回 count 字段：url_read 单次只读 1 个 URL，count 没有 web_search
        那种"命中数"的有意义语义；硬编码 1 会让 FE "找到 N 条"显示矛盾。
        emitter.tool_call_completed 内部还会经 cap_and_truncate(1024) 兜底。
        """
        if result.status != "success":
            return {"kind": "url_read", "truncated": False}
        data = result.data or {}
        return {
            "kind": "url_read",
            "title": data.get("title", ""),
            "favicon": data.get("favicon"),
            "truncated": False,
        }


def _failure_text(data: dict[str, Any]) -> str:
    """服务答复了就用服务的说明；网关、网络出错时服务没有说明，用 Fusion 自己的。"""
    model_text = data.get("model_text")
    if isinstance(model_text, str) and model_text.strip():
        return model_text
    if data.get("failure_kind") == QUOTA_EXHAUSTED_CODE:
        return render_runtime_prompt("tool_handlers.url_read_quota_exhausted")
    return render_runtime_prompt("tool_handlers.url_read_unavailable")


def _safe_log_url(url: str) -> str:
    if not url:
        return ""
    try:
        return evaluate_url_policy(url).safe_log_url or ""
    except Exception:
        return ""


def _meta(data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("meta")
    return meta if isinstance(meta, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
