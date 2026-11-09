"""
agent-tools 网关上自家服务的 MCP 工具：共用的工具目录与调用层。

分工（agent-tools 11-04 起的约定）：工具逻辑、给模型的说明和失败话术归服务，产品只在说明后面接自己的约定
（结果怎么显示、查不到时用哪个工具）。
- 目录：用 tools/list 拿服务的说明和参数（网关不计次）。进程内快照，Run 开始前异步刷新：有快照时不等网络，
  过期就后台刷新、先用旧的；还没有快照时最多等几秒。装配工具时同步读快照；没有快照就不公告这个服务的工具，
  不拿 Fusion 自己写的一份顶上。web_search、url_read 例外：只公告同名的最小定义（只有 query / url），免得模型以为不能联网、
  凭记忆作答；调用照常走网关，失败如实告诉模型。
- 调用：经专用的 McpClientManager（超时、错误分类、响应上限与其他 MCP 一致），只放行代码里写定的网关地址，
  不依赖管理员配置的 MCP 白名单。设备位置这类不该由模型填的信息走 tools/call 的 _meta。
- 结果：成功时 structuredContent 是数据、文字给模型；失败时 structuredContent 是
  {ok: false, error: {code, message, retryable, ...}}，isError 为 false，文字是服务给模型的下一步说明。
  网关或网络出错时没有服务的文字，由调用方给自己的“没有可用结果”说明。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.core.config import settings
from app.core.logger import app_logger as logger
from app.services.external.agent_tools import agent_tools_attribution_headers, agent_tools_url
from app.services.mcp.client import McpClientError, McpClientManager, McpClientPolicy, McpConnectionConfig
from app.services.mcp.provider_profiles import AGENT_TOOLS_CREDENTIAL_REF
from app.services.mcp.tool_contract import sanitize_tool_schema_for_model

# 经网关 MCP 调用的产品服务（工具说明和参数来自各服务的工具目录）。
GATEWAY_MCP_SERVICES: tuple[str, ...] = ("weather", "search", "reader", "flyai", "maps")
# 和 agent-tools 约定的设备位置键：{longitude, latitude, accuracy_m?}，WGS84。
GEOLOCATION_META_KEY = "agent-tools/geolocation"
_CATALOG_TTL_SECONDS = 10 * 60
_RETRY_AFTER_FAILURE_SECONDS = 30.0
_FIRST_LOAD_WAIT_SECONDS = 3.0
# 服务的工具说明比管理员接入的第三方 MCP 长（天气约 4.4k 字），按服务的原文给模型，不截断。
_MAX_DESCRIPTION_CHARS = 8_000


@dataclass(frozen=True)
class GatewayToolSpec:
    service: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class GatewayToolOutcome:
    """一次调用的结果。model_text 为空说明服务没答（网关、网络或协议出错），调用方要给自己的说明。"""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    model_text: str = ""
    error_code: str | None = None
    error: dict[str, Any] = field(default_factory=dict)


def build_gateway_tool_definition(spec: GatewayToolSpec, *, name: str, product_notes: str) -> dict[str, Any]:
    """服务的说明在前、Fusion 的约定接在后面；参数按服务的 schema，保留参数说明。"""

    description = f"{spec.description}\n\n{product_notes}" if product_notes else spec.description
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": sanitize_tool_schema_for_model(spec.input_schema, keep_descriptions=True),
        },
    }


def gateway_connection_config(service: str, tool_names: list[str]) -> McpConnectionConfig:
    return McpConnectionConfig(
        server_id=f"agent-tools:{service}",
        provider=f"agent-tools/{service}",
        endpoint_url=agent_tools_url(service, "/mcp"),
        auth_type="bearer",
        auth_name=None,
        credential_ref=AGENT_TOOLS_CREDENTIAL_REF,
        allowed_tools=tool_names,
    )


@lru_cache(maxsize=1)
def gateway_mcp_manager() -> McpClientManager:
    return McpClientManager(
        policy=McpClientPolicy(
            allowed_hosts=frozenset(),
            allowed_internal_endpoints=frozenset(agent_tools_url(service, "/mcp") for service in GATEWAY_MCP_SERVICES),
            allowed_credential_refs=frozenset({AGENT_TOOLS_CREDENTIAL_REF}),
            connect_timeout_seconds=max(0.1, settings.MCP_CONNECT_TIMEOUT_SECONDS),
            call_timeout_seconds=max(0.1, settings.MCP_CALL_TIMEOUT_SECONDS),
            idempotent_total_timeout_seconds=max(0.1, settings.MCP_IDEMPOTENT_TOTAL_TIMEOUT_SECONDS),
            max_tool_description_chars=_MAX_DESCRIPTION_CHARS,
            max_tool_schema_bytes=max(1_024, settings.MCP_MAX_TOOL_SCHEMA_BYTES),
            max_response_bytes=max(4_096, settings.MCP_MAX_RESPONSE_BYTES),
        )
    )


async def call_gateway_tool(
    service: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    scene: str,
    meta: Mapping[str, Any] | None = None,
    min_call_timeout_seconds: float | None = None,
    manager: McpClientManager | None = None,
) -> GatewayToolOutcome:
    try:
        payload = await (manager or gateway_mcp_manager()).call_tool(
            gateway_connection_config(service, [tool_name]),
            tool_name,
            arguments,
            min_call_timeout_seconds=min_call_timeout_seconds,
            request_headers=agent_tools_attribution_headers(scene),
            meta=meta,
        )
    except McpClientError as error:
        # 网关 429（产品当天额度用完）归为 rate_limited；都没有服务的文字。
        return GatewayToolOutcome(ok=False, error_code=error.code)
    return parse_gateway_tool_payload(payload)


def parse_gateway_tool_payload(payload: Mapping[str, Any]) -> GatewayToolOutcome:
    text = "".join(
        block["text"]
        for block in payload.get("content") or []
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    structured = payload.get("structuredContent")
    if not isinstance(structured, Mapping):
        return GatewayToolOutcome(ok=False, error_code="invalid_response")
    if structured.get("ok") is False:
        error = structured.get("error")
        error = dict(error) if isinstance(error, Mapping) else {}
        code = error.get("code")
        return GatewayToolOutcome(
            ok=False,
            model_text=text,
            error_code=code if isinstance(code, str) and code else "tool_error",
            error=error,
        )
    return GatewayToolOutcome(ok=True, data=dict(structured), model_text=text)


ListTools = Callable[[str], Awaitable[list[dict[str, Any]]]]


async def _list_gateway_tools(service: str) -> list[dict[str, Any]]:
    return await gateway_mcp_manager().list_tools(gateway_connection_config(service, []))


class GatewayToolCatalog:
    """网关服务 tools/list 的进程内快照；读同步，刷新异步，刷新失败时保留旧快照。"""

    def __init__(
        self,
        *,
        services: tuple[str, ...] = GATEWAY_MCP_SERVICES,
        list_tools: ListTools = _list_gateway_tools,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = _CATALOG_TTL_SECONDS,
        retry_after_failure_seconds: float = _RETRY_AFTER_FAILURE_SECONDS,
        first_load_wait_seconds: float = _FIRST_LOAD_WAIT_SECONDS,
    ) -> None:
        self._services = services
        self._list_tools = list_tools
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._retry_after_failure_seconds = retry_after_failure_seconds
        self._first_load_wait_seconds = first_load_wait_seconds
        self._snapshots: dict[str, tuple[float, dict[str, GatewayToolSpec]]] = {}
        self._failed_at: dict[str, float] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def spec(self, service: str, tool_name: str) -> GatewayToolSpec | None:
        snapshot = self._snapshots.get(service)
        return None if snapshot is None else snapshot[1].get(tool_name)

    async def refresh(self) -> None:
        """Run 开始前调用。已有快照时从不等网络；还没有时最多等 first_load_wait_seconds，等不到本次就不公告。"""

        loop = asyncio.get_running_loop()
        waiting: list[asyncio.Task[None]] = []
        for service in self._services:
            now = self._clock()
            snapshot = self._snapshots.get(service)
            if snapshot is not None and now - snapshot[0] < self._ttl_seconds:
                continue
            failed_at = self._failed_at.get(service)
            if failed_at is not None and now - failed_at < self._retry_after_failure_seconds:
                continue
            task = self._tasks.get(service)
            if task is None or task.done() or task.get_loop() is not loop:
                task = loop.create_task(self._load(service), name=f"agent_tools_catalog:{service}")
                self._tasks[service] = task
            if snapshot is None:
                waiting.append(task)
        if waiting:
            await asyncio.wait(waiting, timeout=self._first_load_wait_seconds)

    async def _load(self, service: str) -> None:
        try:
            tools = await self._list_tools(service)
        except Exception as error:  # noqa: BLE001 — 目录读不到只影响本服务的工具是否公告，不中断 Run
            logger.warning(
                "agent-tools 工具目录读取失败: service=%s error_code=%s",
                service,
                getattr(error, "code", type(error).__name__),
            )
            self._failed_at[service] = self._clock()
            return
        specs = {
            tool["name"]: GatewayToolSpec(
                service=service,
                name=tool["name"],
                description=tool.get("description") or "",
                input_schema=tool["input_schema"],
            )
            for tool in tools
        }
        self._snapshots[service] = (self._clock(), specs)
        self._failed_at.pop(service, None)


_CATALOG = GatewayToolCatalog()


def gateway_tool_catalog() -> GatewayToolCatalog:
    return _CATALOG


async def refresh_gateway_tool_catalog() -> None:
    """runner 在装配工具前调用；永不抛错。没配产品密钥时什么也不做（本地、测试环境）。"""

    if not settings.AGENT_TOOLS_API_KEY.strip():
        return
    try:
        await _CATALOG.refresh()
    except Exception as error:  # noqa: BLE001 — 目录刷新不影响 Run 本身
        logger.warning("agent-tools 工具目录刷新异常: error_type=%s", type(error).__name__)
