"""generate_image：经 agent-tools 网关调用生图服务（/image/v2/mcp）的 generate_image。

出图、存图、改图找原图、比例、缓存、给模型的说明和失败话术都在服务里（agent-tools services/image 的 README
「产品用的生图工具」），Fusion 和灵稿共用。图存在服务里、归 Fusion，结果里只有图片编号；页面要看时 Fusion 后端
按编号经网关取（见 app.api.generated_images）。这里只做 Fusion 自己的事：
- 说明用服务原文，后面接 Fusion 的一段（image.product_notes）；服务目录里没有就不公告。
- 改图只认这段会话里画的、还记在 generated_images 里的图：别的编号（编的、别的会话的、已删的）不交给服务，
  照找不到原图告诉模型，不算画了一次。
- 一个 Run 最多画 MAX_IMAGES_PER_RUN 张：发出去就可能花了钱，没画成的也算；服务说参数不对、找不到原图，
  或网关当天额度用完（没转给服务）的不算。
- 画成的记进 generated_images（页面取图、删会话时用），画成图片卡片；给模型的是服务的那段英文，后面接一句卡片上已标了模型。
- 没画成：服务的说明照给模型；网关、网络出错没有服务的文字时，用 Fusion 的说明。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.db.database import SessionLocal
from app.schemas.chat import GeneratedImageBlock
from app.services import generated_images
from app.services.external.agent_tools_images import IMAGE_MIME_TYPES, is_image_id
from app.services.external.agent_tools_mcp import (
    GatewayToolOutcome,
    GatewayToolSpec,
    build_gateway_tool_definition,
    call_gateway_tool,
)
from app.services.mcp.tool_contract import canonical_json_bytes, validate_tool_arguments
from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext

IMAGE_SERVICE = "image"
GENERATE_IMAGE_TOOL_NAME = "generate_image"
# 生图有真实费用：同一 Run 最多画几张。
MAX_IMAGES_PER_RUN = 3
# 服务整条主备链限时 135 秒（含找参考图、各模型尝试），超时自己回 upstream_timeout；这里留出传输余量。网关转发限 180 秒。
_CALL_TIMEOUT_SECONDS = 150.0
EXECUTION_TIMEOUT_SECONDS = 180.0
# 服务没去画（不花钱）的错误码；rate_limited 是网关当天额度用完，请求没转给服务。
_NOT_ATTEMPTED = frozenset({"invalid_params", "source_image_unavailable", "unauthorized", "rate_limited"})
# Fusion 自己回的失败（服务没给文字）→ 给模型的说明键；没列的（网关、网络出错）用 image.unavailable。
_FUSION_FAILURE_TEXTS = {
    "source_image_unavailable": "image.source_image_unavailable",
    "run_image_limit_reached": "image.run_image_limit_reached",
    "rate_limited": "image.quota_exhausted",
    "invalid_response": "image.failed",
    "image_register_failed": "image.failed",
    "source_image_lookup_failed": "image.failed",
}
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_ASPECT_RATIO_RE = re.compile(r"^\d{1,3}:\d{1,3}$")
_MAX_PROMPT_CHARS = 1_000
_MAX_CONTEXT_BYTES = 12_000


class ImageRunControls:
    """单次 Run 的生图次数：调用前占一次，服务没去画时退回。"""

    def __init__(self, *, max_images: int = MAX_IMAGES_PER_RUN) -> None:
        self._max_images = max_images
        self._used = 0
        self._lock = asyncio.Lock()

    async def try_reserve(self) -> bool:
        async with self._lock:
            if self._used >= self._max_images:
                return False
            self._used += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            self._used = max(0, self._used - 1)


@dataclass(frozen=True)
class ImageToolBinding:
    """服务端为本 Run 绑定的会话与用户；模型参数改变不了图归谁。"""

    alias: str
    remote_tool_name: str
    provider: str
    tool_label: str
    definition_sha256: str
    conversation_id: str
    user_id: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "remote_tool_name": self.remote_tool_name,
            "provider": self.provider,
            "tool_label": self.tool_label,
            "definition_sha256": self.definition_sha256,
        }


def build_generate_image_definition(spec: GatewayToolSpec) -> dict[str, Any]:
    return build_gateway_tool_definition(
        spec,
        name=GENERATE_IMAGE_TOOL_NAME,
        product_notes=render_runtime_prompt("image.product_notes"),
    )


def build_image_binding(definition: dict[str, Any], *, conversation_id: str, user_id: str) -> ImageToolBinding:
    return ImageToolBinding(
        alias=GENERATE_IMAGE_TOOL_NAME,
        remote_tool_name=GENERATE_IMAGE_TOOL_NAME,
        provider=IMAGE_SERVICE,
        tool_label="图片生成",
        definition_sha256=hashlib.sha256(canonical_json_bytes(definition)).hexdigest(),
        conversation_id=conversation_id,
        user_id=user_id,
    )


async def _conversation_has_image(*, conversation_id: str, image_id: str) -> bool:
    def lookup() -> bool:
        db = SessionLocal()
        try:
            return generated_images.conversation_has_image(db, conversation_id=conversation_id, image_id=image_id)
        finally:
            db.close()

    return await asyncio.to_thread(lookup)


async def _register_image(**kwargs: Any) -> None:
    def register() -> None:
        db = SessionLocal()
        try:
            generated_images.register_generated_image(db, **kwargs)
        finally:
            db.close()

    await asyncio.to_thread(register)


class ImageToolHandler(BaseToolHandler):
    # 生图有费用且非幂等：不自动重试，也不做运行内成功复用。
    supports_automatic_retry = False
    execution_timeout_seconds = EXECUTION_TIMEOUT_SECONDS

    def __init__(
        self,
        *,
        binding: ImageToolBinding,
        parameters: dict[str, Any],
        controls: ImageRunControls | None = None,
        call_tool: Callable[..., Awaitable[GatewayToolOutcome]] = call_gateway_tool,
        conversation_has_image: Callable[..., Awaitable[bool]] = _conversation_has_image,
        register_image: Callable[..., Awaitable[None]] = _register_image,
        max_llm_context_bytes: int = _MAX_CONTEXT_BYTES,
    ) -> None:
        self.binding = binding
        self.parameters = parameters
        self.controls = controls or ImageRunControls()
        self.call_tool = call_tool
        self.conversation_has_image = conversation_has_image
        self.register_image = register_image
        self.max_llm_context_bytes = max_llm_context_bytes

    @property
    def tool_name(self) -> str:
        return self.binding.alias

    @property
    def sse_event_prefix(self) -> str:
        return "mcp"

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        """只按公告的 schema 校验形状；比例、原图编号是否可用由服务判断并告诉模型。"""
        if not isinstance(args, dict):
            return [{"field": "request", "code": "invalid_arguments"}]
        return validate_tool_arguments(self.parameters, args)

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        del context
        started_at = time.monotonic()
        arguments = dict(args)
        source = arguments.get("source_image_id")
        source = source.strip() if isinstance(source, str) else ""
        if source:
            try:
                owned = await self.conversation_has_image(
                    conversation_id=self.binding.conversation_id,
                    image_id=source,
                )
            except Exception as error:  # noqa: BLE001 — 查库故障如实报失败，不中断本轮
                logger.warning("查找待编辑的生成图失败: error_type=%s", type(error).__name__)
                return self._failed(started_at, "source_image_lookup_failed")
            if not owned:
                return self._failed(started_at, "source_image_unavailable")
            arguments["source_image_id"] = source
        if not await self.controls.try_reserve():
            return self._failed(started_at, "run_image_limit_reached")

        outcome = await self.call_tool(
            IMAGE_SERVICE,
            GENERATE_IMAGE_TOOL_NAME,
            arguments,
            scene="image_edit" if source else "chat_image",
            min_call_timeout_seconds=_CALL_TIMEOUT_SECONDS,
        )
        if not outcome.ok:
            error_code = outcome.error_code or "tool_error"
            if error_code in _NOT_ATTEMPTED:
                await self.controls.release()
            if not outcome.model_text:
                logger.warning("生图服务没有答复: error_code=%s", error_code)
            return self._failed(started_at, error_code, model_text=outcome.model_text)

        image = _service_image(outcome.data.get("image"))
        if image is None or not outcome.model_text:
            logger.warning("生图结果的格式不对")
            raw = outcome.data.get("image")
            raw_id = raw.get("id") if isinstance(raw, dict) else None
            if is_image_id(raw_id):
                generated_images.schedule_image_deletion({raw_id})
            return self._failed(started_at, "invalid_response")
        try:
            await self.register_image(
                image_id=image["id"],
                conversation_id=self.binding.conversation_id,
                user_id=self.binding.user_id,
                mime_type=image["mime_type"],
            )
        except Exception as error:  # noqa: BLE001 — 记不上就取不到也删不掉：如实报失败，图从服务里删掉
            logger.warning("生成图没登记上: error_type=%s", type(error).__name__)
            generated_images.schedule_image_deletion({image["id"]})
            return self._failed(started_at, "image_register_failed")

        reference = outcome.data.get("reference_search")
        aspect_ratio = arguments.get("aspect_ratio")
        raw_prompt = str(arguments.get("prompt") or "")
        # schema 只保证非空：全是空白时原样保留，卡片的描述字段不能为空
        prompt = (raw_prompt.strip() or raw_prompt)[:_MAX_PROMPT_CHARS]
        return ToolResult(
            status="success",
            duration_ms=_duration_ms(started_at),
            data={
                "remote_tool_name": GENERATE_IMAGE_TOOL_NAME,
                "image_id": image["id"],
                "mime_type": image["mime_type"],
                "width": image["width"],
                "height": image["height"],
                "model": image["model"],
                "prompt": prompt,
                "aspect_ratio": aspect_ratio
                if isinstance(aspect_ratio, str) and _ASPECT_RATIO_RE.fullmatch(aspect_ratio)
                else None,
                "source_image_id": source or None,
                "reference_search_status": reference.get("status") if isinstance(reference, dict) else None,
                "model_text": outcome.model_text,
            },
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> GeneratedImageBlock | None:
        if result.status != "success":
            return None
        data = result.data
        return GeneratedImageBlock(
            type="generated_image",
            id=block_id,
            schema_version=2,
            provider=IMAGE_SERVICE,
            image_id=data["image_id"],
            mime_type=data["mime_type"],
            width=data["width"],
            height=data["height"],
            prompt=data["prompt"],
            aspect_ratio=data.get("aspect_ratio"),
            model=data.get("model"),
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        model_text = data.get("model_text") if isinstance(data.get("model_text"), str) else ""
        if result.status == "success":
            text = f"{model_text} {render_runtime_prompt('image.shown')}"
        else:
            error_code = data.get("error_code")
            text = model_text or render_runtime_prompt(
                _FUSION_FAILURE_TEXTS.get(str(error_code), "image.unavailable"),
                max_images=MAX_IMAGES_PER_RUN,
            )
        return text.encode()[: self.max_llm_context_bytes].decode(errors="ignore")

    def sanitize_output_data_for_log(self, result: ToolResult) -> dict:
        data = result.data or {}
        output: dict[str, Any] = {"remote_tool_name": GENERATE_IMAGE_TOOL_NAME, "status": result.status}
        for key in ("image_id", "mime_type", "width", "height", "model", "source_image_id", "reference_search_status"):
            if data.get(key) is not None:
                output[key] = data[key]
        error_code = data.get("error_code")
        if isinstance(error_code, str) and _ERROR_CODE_RE.fullmatch(error_code):
            output["error_code"] = error_code
        return output

    def trajectory_output_data(self, result: ToolResult) -> dict:
        return self.sanitize_output_data_for_log(result)

    def _build_result_summary(self, result: ToolResult) -> dict:
        data = result.data or {}
        summary: dict[str, Any] = {"kind": "generated_image", "truncated": False}
        if result.status == "success":
            summary["image_id"] = data.get("image_id")
        else:
            summary["error_code"] = data.get("error_code")
        return summary

    def _failed(self, started_at: float, error_code: str, *, model_text: str = "") -> ToolResult:
        return ToolResult(
            status="failed",
            duration_ms=_duration_ms(started_at),
            data={
                "remote_tool_name": GENERATE_IMAGE_TOOL_NAME,
                "error_code": error_code if _ERROR_CODE_RE.fullmatch(error_code) else "tool_error",
                "model_text": model_text,
            },
            error_message="图片生成失败",
        )


def _service_image(value: Any) -> dict[str, Any] | None:
    """服务结果里的 image（见 image-service 的 StoredImage）；只取卡片要的，格式不对返回 None。"""
    if not isinstance(value, dict):
        return None
    image_id = value.get("id")
    mime_type = value.get("mime_type")
    width = value.get("width")
    height = value.get("height")
    if (
        not is_image_id(image_id)
        or mime_type not in IMAGE_MIME_TYPES
        or not isinstance(width, int)
        or not isinstance(height, int)
        or not 0 < width <= 20_000
        or not 0 < height <= 20_000
    ):
        return None
    model = value.get("model")
    return {
        "id": image_id,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "model": model if isinstance(model, str) and _MODEL_ID_RE.fullmatch(model) else None,
    }


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1_000))
