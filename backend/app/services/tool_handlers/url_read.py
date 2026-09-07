"""
UrlReadHandler — 网页读取工具处理器
"""

import time

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.config import settings
from app.schemas.chat import SourceReference, UrlBlock
from app.services.agent.sanitizer import URL_READ_REASON_MAX_CHARS, sanitize_url_read_arguments
from app.services.agent_strategy_config import get_agent_strategy_config
from app.services.external.reader_client import read_url_with_diagnostics
from app.services.security.url_policy import evaluate_url_policy
from app.services.source_context import UntrustedSourceContext, format_untrusted_source_context
from app.services.tool_handlers.base import BaseToolHandler, ToolResult
from app.services.tool_handlers.url_read_body import select_article_body

# 注入 LLM 上下文时的最大字符数（约 4000 token）
MAX_CONTENT_CHARS = 8000
MAX_REASON_CHARS = URL_READ_REASON_MAX_CHARS


class UrlReadHandler(BaseToolHandler):
    supports_run_level_citations = True

    @property
    def tool_name(self) -> str:
        return "url_read"

    @property
    def sse_event_prefix(self) -> str:
        return "url_read"

    def sanitize_input_params_for_log(self, input_params: dict) -> dict:
        return sanitize_url_read_arguments(input_params)

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        data = result.data if isinstance(result.data, dict) else {}
        safe_output = {}
        raw_url = data.get("safe_log_url") or data.get("url")
        if isinstance(raw_url, str):
            try:
                safe_output["url"] = evaluate_url_policy(raw_url).safe_log_url or ""
            except Exception:
                safe_output["url"] = ""

        for key in ("failure_kind", "degraded_reason"):
            value = data.get(key)
            if isinstance(value, str):
                safe_output[key] = value[:80]
        for key in ("retryable", "budget_limited"):
            value = data.get(key)
            if isinstance(value, bool):
                safe_output[key] = value
        for key in (
            "upstream_status",
            "attempts",
            "reader_duration_ms",
            "content_length",
            "reader_fetch_ms",
        ):
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_output[key] = value
        return safe_output

    async def execute(self, args: dict) -> ToolResult:
        raw_url = args.get("url", "")
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        reason = _normalize_reason(args.get("reason"))
        if not url:
            return ToolResult(
                status="degraded",
                error_message="url 为空",
                data={"url": url, "reason": reason},
            )
        try:
            policy = evaluate_url_policy(url)
        except Exception:
            return ToolResult(
                status="degraded",
                error_message="链接格式无效，已跳过读取",
                data={
                    "url": "",
                    "safe_log_url": None,
                    "degraded_reason": "invalid_url",
                    "reason": reason,
                },
            )
        if not policy.allowed:
            return ToolResult(
                status="degraded",
                error_message=policy.user_visible_message or "URL 不允许读取",
                data={
                    "url": policy.safe_log_url or "",
                    "safe_log_url": policy.safe_log_url,
                    "degraded_reason": policy.reason,
                    "reason": reason,
                },
            )

        start = time.monotonic()
        try:
            response = await read_url_with_diagnostics(
                policy.normalized_url or url,
                timeout=settings.READER_SERVICE_TIMEOUT,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            result = response.result

            if result is None:
                failure = response.failure
                return ToolResult(
                    status="degraded",
                    duration_ms=duration_ms,
                    error_message=failure.message if failure else "网页暂时无法读取，已跳过该来源",
                    data={
                        "url": policy.safe_log_url or policy.normalized_url or "",
                        "safe_log_url": policy.safe_log_url,
                        "reason": reason,
                        "failure_kind": failure.kind if failure else "empty_result",
                        "retryable": failure.retryable if failure else False,
                        "upstream_status": failure.upstream_status if failure else None,
                        "attempts": failure.attempts if failure else 1,
                        "reader_duration_ms": failure.reader_duration_ms if failure else None,
                        "reader_diagnostic": failure.reader_diagnostic if failure else None,
                    },
                )

            return ToolResult(
                status="success",
                duration_ms=duration_ms,
                data={
                    "url": result.url,
                    "title": result.title,
                    "content": result.content,
                    "favicon": result.favicon,
                    "content_length": result.content_length,
                    "reader_fetch_ms": result.fetch_ms,
                    "attempts": result.attempts,
                    "reason": reason,
                },
            )
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ToolResult(
                status="degraded",
                duration_ms=duration_ms,
                error_message="网页读取发生异常，已跳过该来源",
                data={
                    "url": policy.safe_log_url or "",
                    "safe_log_url": policy.safe_log_url,
                    "reason": reason,
                    "failure_kind": "unknown",
                    "retryable": False,
                    "upstream_status": None,
                    "attempts": 1,
                    "reader_duration_ms": None,
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
            reason=result.data.get("reason"),
        )

    def format_llm_context(
        self,
        result: ToolResult,
        *,
        citation_numbers: list[int] | None = None,
    ) -> str:
        url = result.data.get("url", "")
        title = result.data.get("title", "")
        content = result.data.get("content", "")

        if result.status != "success" or not content:
            unavailable_message = render_runtime_prompt("tool_handlers.url_read_unavailable")
            if result.data.get("failure_kind") in {"empty_content", "access_blocked"}:
                unavailable_message += "\n" + render_runtime_prompt(
                    "tool_handlers.url_read_failure_reason", failure_kind=result.data["failure_kind"]
                )
            return format_untrusted_source_context(
                UntrustedSourceContext(
                    source_id="url-read-unavailable",
                    source_type="url_read",
                    title=title or "Page read failed",
                    url=url,
                    content=unavailable_message,
                    provider="web",
                ),
                max_chars=MAX_CONTENT_CHARS + 100,
            )

        # 规范 reader 包装中优先从精确标题对应的正文取窗口，原始结果仍完整保留。
        content = select_article_body(content, title)
        truncated = False
        max_content_chars = _tool_context_int("url_read_max_content_chars", MAX_CONTENT_CHARS)
        if len(content) > max_content_chars:
            content = content[:max_content_chars]
            truncated = True

        if truncated:
            content = f"{content}\n{render_runtime_prompt('shared.truncated')}"

        # 编号由运行期注册表分配，并与持久化 source_refs 共享，不能按读页次数重置。
        citation_number = citation_numbers[0] if citation_numbers else None
        if not isinstance(citation_number, int) or isinstance(citation_number, bool) or citation_number < 1:
            citation_number = None
        context = format_untrusted_source_context(
            UntrustedSourceContext(
                source_id=str(citation_number) if citation_number is not None else "url-read-unassigned",
                source_type="url_read",
                title=title or "Unknown",
                url=url,
                content=content,
                provider="web",
            ),
            max_chars=max_content_chars + 100,
        )
        return f"[{citation_number}]\n{context}" if citation_number is not None else context

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


def _normalize_reason(value) -> str | None:
    if not isinstance(value, str):
        return None
    reason = value.strip()
    if not reason:
        return None
    return reason[: _tool_context_int("url_read_max_reason_chars", MAX_REASON_CHARS)]


def _tool_context_int(key: str, fallback: int) -> int:
    try:
        strategy_config, _meta = get_agent_strategy_config()
        return max(1, int((strategy_config.get("tool_context") or {}).get(key, fallback)))
    except (TypeError, ValueError):
        return fallback
