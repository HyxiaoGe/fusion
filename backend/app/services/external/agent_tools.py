"""
agent-tools 网关：生图、搜索、网页读取统一走一个地址和一把 Fusion 产品密钥。

网关按密钥认定产品，按 X-Caller-User / X-Caller-Scene / X-Request-ID 记账；
用户与 run 取自当前运行的日志上下文，密钥只放进请求头，不进日志或模型可见内容。
"""

from app.core.config import settings
from app.core.logger import current_log_context


def agent_tools_url(service: str, path: str) -> str:
    return f"{settings.AGENT_TOOLS_URL.rstrip('/')}/{service}{path}"


def agent_tools_headers(scene: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.AGENT_TOOLS_API_KEY}", **agent_tools_attribution_headers(scene)}


def agent_tools_attribution_headers(scene: str) -> dict[str, str]:
    """只有记账用的请求头、不带密钥：经 MCP 管理器调用时密钥由管理器按凭证引用加。"""

    headers = {"X-Caller-Scene": scene}
    user_id = current_log_context("user_id")
    if user_id:
        headers["X-Caller-User"] = user_id
    run_id = current_log_context("run_id")
    if run_id:
        headers["X-Request-ID"] = run_id
    return headers
