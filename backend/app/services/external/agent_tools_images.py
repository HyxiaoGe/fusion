"""
agent-tools 网关上 image-service 存着的图：按编号取、删（见 agent-tools 的 services/image/README.md「产品用的生图工具」）。

generate_image（/image/v2/mcp）画好的图存在服务里、归 Fusion，结果里只有图片编号：
- GET /image/v1/images/{id} → 图的内容。页面来取时由 Fusion 后端转给浏览器，不暴露服务地址；别的产品的、不存在的都是 404。
- DELETE /image/v1/images/{id} → 删会话、重新回答、编辑问题时删掉不要的图。服务不自动清理；删过再删、本来就没有都回 204。
这两个网关都不算调用、不占每日上限。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeGuard

import httpx

from app.core.logger import app_logger as logger
from app.services.external.agent_tools import agent_tools_headers, agent_tools_url

# image-service 的图片编号（uuid4）
IMAGE_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_FETCH_TIMEOUT_SECONDS = 30.0
_DELETE_TIMEOUT_SECONDS = 10.0


class ImageStoreError(RuntimeError):
    """网关或服务出错，取不到图（不是没有这张图）。"""


def is_image_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and IMAGE_ID_PATTERN.fullmatch(value) is not None


def _image_url(image_id: str) -> str:
    return agent_tools_url("image", f"/v1/images/{image_id}")


async def fetch_image(
    image_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str] | None:
    """取一张图：(内容, MIME 类型)；服务里没有（删了、不是 Fusion 的）是 None，网关或服务出错抛 ImageStoreError。"""

    if not is_image_id(image_id):
        return None
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS, trust_env=False, transport=transport) as client:
            async with client.stream(
                "GET", _image_url(image_id), headers=agent_tools_headers("image_view")
            ) as response:
                if response.status_code == 404:
                    return None
                mime_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if response.status_code != 200 or mime_type not in IMAGE_MIME_TYPES:
                    raise ImageStoreError(f"取图失败：HTTP {response.status_code}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise ImageStoreError("图片超过大小上限")
                    chunks.append(chunk)
    except httpx.HTTPError as error:
        raise ImageStoreError(f"取图失败：{type(error).__name__}") from None
    return b"".join(chunks), mime_type


def delete_images(image_ids: Iterable[str], *, transport: httpx.BaseTransport | None = None) -> None:
    """删掉这些图（同步，在后台线程里跑）。删不掉只记日志：留下的图没人指着，只占服务的磁盘。"""

    ids = [image_id for image_id in image_ids if is_image_id(image_id)]
    if not ids:
        return
    with httpx.Client(timeout=_DELETE_TIMEOUT_SECONDS, trust_env=False, transport=transport) as client:
        for image_id in ids:
            try:
                response = client.delete(_image_url(image_id), headers=agent_tools_headers("image_delete"))
            except httpx.HTTPError as error:
                logger.warning("删图没成功: image_id=%s error_type=%s", image_id, type(error).__name__)
                continue
            if response.status_code != 204:
                logger.warning("删图没成功: image_id=%s status=%s", image_id, response.status_code)
