"""游客的联网搜索：每次先扣今日搜索额度，用完后把额度用完作为工具结果告诉模型。"""

import asyncio

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.services.guest_quota import (
    GUEST_SEARCH_EXHAUSTED_CODE,
    GUEST_SEARCH_EXHAUSTED_MESSAGE,
    reserve_guest_search,
)
from app.services.tool_handlers.base import ToolResult, ToolRuntimeContext
from app.services.tool_handlers.web_search import WebSearchHandler


class GuestWebSearchHandler(WebSearchHandler):
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._exhausted = False

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        try:
            allowed = await asyncio.to_thread(reserve_guest_search, self.user_id)
        except Exception:  # noqa: BLE001 — 额度服务不可用时不放开搜索
            logger.warning("guest_search_quota_unavailable user=%s", self.user_id, exc_info=True)
            allowed = False
        if not allowed:
            self._exhausted = True
            logger.info("guest_search_quota_exhausted user=%s", self.user_id)
            return ToolResult(
                status="failed",
                error_message=GUEST_SEARCH_EXHAUSTED_MESSAGE,
                data={
                    "error_code": GUEST_SEARCH_EXHAUSTED_CODE,
                    "retryable": False,
                    "query": args.get("query", ""),
                    "sources": [],
                    "result_count": 0,
                },
            )
        return await super().execute(args, context)

    async def is_run_budget_exhausted(self) -> bool:
        """额度用完后本次 run 不再向模型公告搜索工具。"""
        return self._exhausted

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        if result.data.get("error_code") == GUEST_SEARCH_EXHAUSTED_CODE:
            query_context = render_runtime_prompt("tool_handlers.search_query", query=result.data.get("query", ""))
            return f"{query_context}\n{GUEST_SEARCH_EXHAUSTED_MESSAGE}"
        return super().format_llm_context(result, citation_numbers=citation_numbers)
