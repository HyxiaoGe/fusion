from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from app.api.deps import get_current_admin_user, get_mcp_server_service
from app.core.config import settings
from app.db.models import User as UserModel
from app.schemas.mcp import (
    McpServerCreate,
    McpServerResponse,
    McpServerStatusRequest,
    McpServerUpdate,
)
from app.schemas.response import success
from app.services.mcp.agent_tools import default_circuit_breaker
from app.services.mcp.server_service import McpServerService

router = APIRouter()


@router.get("/servers")
async def list_mcp_servers(
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success([_serialize(row) for row in service.list_servers()])


@router.post("/servers")
async def create_mcp_server(
    request: McpServerCreate,
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success(_serialize(service.create_server(request)))


@router.patch("/servers/{server_id}")
async def update_mcp_server(
    server_id: str,
    request: McpServerUpdate,
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success(_serialize(service.update_server(server_id, request)))


@router.post("/servers/{server_id}/status")
async def update_mcp_server_status(
    server_id: str,
    request: McpServerStatusRequest,
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success(_serialize(service.set_status(server_id, request)))


@router.post("/servers/{server_id}/test")
async def test_mcp_server(
    server_id: str,
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success(_serialize(await service.test_server(server_id)))


@router.post("/servers/{server_id}/tools/refresh")
async def refresh_mcp_server_tools(
    server_id: str,
    _admin: UserModel = Depends(get_current_admin_user),
    service: McpServerService = Depends(get_mcp_server_service),
):
    return success(_serialize(await service.refresh_tools(server_id)))


PROBE_FRESHNESS_TTL = timedelta(seconds=settings.MCP_PROBE_FRESHNESS_TTL_SECONDS)
# 熔断状态存在本进程内存里，多 worker 下各进程独立；显式标注作用域，避免管理视图
# 把它当成全局实时健康（issue #32）。
RUNTIME_CIRCUIT_SCOPE = "process"


def resolve_probe_freshness(last_checked_at: datetime | None, *, now: datetime | None = None) -> str:
    """探测结果的新鲜度；health_status 本身只说明上次探测的结论。"""

    if last_checked_at is None:
        return "never"
    reference = now or datetime.now(UTC)
    checked_at = last_checked_at if last_checked_at.tzinfo is not None else last_checked_at.replace(tzinfo=UTC)
    return "fresh" if reference - checked_at <= PROBE_FRESHNESS_TTL else "stale"


def _serialize(row) -> McpServerResponse:
    response = McpServerResponse.model_validate(row)
    return response.model_copy(
        update={
            "probe_freshness": resolve_probe_freshness(response.last_checked_at),
            "runtime_circuit_state": default_circuit_breaker().snapshot(response.id),
            "runtime_circuit_scope": RUNTIME_CIRCUIT_SCOPE,
        }
    )
