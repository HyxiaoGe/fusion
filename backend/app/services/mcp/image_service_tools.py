"""image-service 生图产品工具。

image-service 是部署在内网的自建 MCP，``generate_image`` 返回一段文本，其中的图片地址
只在内网可达且为 HTTP，不能交给模型或浏览器。这里按服务端点的内网地址下载图片，
转存到 Fusion 文件存储，再以 ``generated_image`` 块展示；模型只拿到成功或失败的事实。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
from app.core.logger import app_logger as logger
from app.schemas.chat import GeneratedImageBlock
from app.services.mcp.client import McpClientError
from app.services.mcp.tool_contract import canonical_json_bytes
from app.services.tool_handlers.base import BaseToolHandler, ToolResult

IMAGE_SERVICE_PROVIDER = "image-service"
GENERATE_IMAGE_TOOL_NAME = "generate_image"
IMAGE_SERVICE_REMOTE_TOOL_NAME = "generate_image"

# image-service 所有 Gemini 模型都接受的比例；Seedream 接受任意比例。
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
# 主模型被内容审核拦截时，由 image-service 换这个模型重试一次（其他错误不切换）。
FALLBACK_MODEL = "doubao-seedream-4-5"
# image-service 结构化错误码（P0 设计 §2.1）；不认识的码按通用上游失败处理。
KNOWN_ERROR_CODES = frozenset({
    "invalid_params",
    "model_not_found",
    "content_filtered",
    "upstream_timeout",
    "upstream_unavailable",
    "upstream_error",
    "no_image_returned",
    "config_missing",
    "generation_in_progress",
    "internal_error",
})
MAX_PROMPT_CHARS = 1_000
MAX_STYLE_CHARS = 60
# 生图有真实费用：同一 run 最多生成的张数。
MAX_IMAGES_PER_RUN = 3
# 实测 Gemini Flash 约 15 秒，Pro 与 Seedream 更慢；image-service 自身上游超时为 300 秒。
MCP_CALL_TIMEOUT_SECONDS = 150.0
EXECUTION_TIMEOUT_SECONDS = 180.0
DOWNLOAD_TIMEOUT_SECONDS = 30.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# image-service 的 MCP 文本返回协议（src/mcp/server.py），按行解析。
_GENERATED_LINE = re.compile(r"^Generated image:\s*(\S+)\s*$", re.MULTILINE)
_MODEL_LINE = re.compile(r"^Model:\s*([A-Za-z0-9._:-]{1,80})\s*$", re.MULTILINE)
_MODEL_ID = re.compile(r"[A-Za-z0-9._:-]{1,80}")
_ERROR_PREFIX = "Error"
_IMAGE_PATH = re.compile(r"^/static/images/[A-Za-z0-9_-]{1,128}\.(?:png|jpe?g|webp)$")
_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

StoreImageFn = Callable[..., Any]


@dataclass(frozen=True)
class ImageServiceToolBinding:
    """服务端为本 Run 绑定的服务与写入归属；模型参数无法改变会话或用户。"""

    alias: str
    server_id: str
    provider: str
    remote_tool_name: str
    config_version: int
    tool_label: str
    definition_sha256: str
    endpoint_url: str
    conversation_id: str
    user_id: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "server_id": self.server_id,
            "remote_tool_name": self.remote_tool_name,
            "provider": self.provider,
            "config_version": self.config_version,
            "tool_label": self.tool_label,
            "definition_sha256": self.definition_sha256,
        }


class ImageGenerationError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, fallback_from: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        # 主模型被拦截后备用模型也失败时，记主模型的失败码。
        self.fallback_from = fallback_from


@dataclass(frozen=True)
class GeneratedImageOutcome:
    image_path: str
    model: str | None
    requested_model: str | None = None
    fallback_used: bool = False


def is_image_service_row(row: Any) -> bool:
    return str(getattr(row, "provider", "")).strip().lower() == IMAGE_SERVICE_PROVIDER


def build_generate_image_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": GENERATE_IMAGE_TOOL_NAME,
            "description": render_runtime_prompt("image_service.tool_description"),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_PROMPT_CHARS,
                        "description": render_runtime_prompt("image_service.prompt"),
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": list(ASPECT_RATIOS),
                        "description": render_runtime_prompt("image_service.aspect_ratio"),
                    },
                    "style": {
                        "type": "string",
                        "maxLength": MAX_STYLE_CHARS,
                        "description": render_runtime_prompt("image_service.style"),
                    },
                    "regenerate": {
                        "type": "boolean",
                        "description": render_runtime_prompt("image_service.regenerate"),
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
    }


def build_image_service_binding(
    *,
    row: Any,
    remote_definition_sha256: str,
    conversation_id: str,
    user_id: str,
) -> ImageServiceToolBinding:
    definition_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "definition": build_generate_image_definition(),
                "dependencies": {IMAGE_SERVICE_REMOTE_TOOL_NAME: remote_definition_sha256},
            }
        )
    ).hexdigest()
    return ImageServiceToolBinding(
        alias=GENERATE_IMAGE_TOOL_NAME,
        server_id=str(row.id),
        provider=str(row.provider),
        remote_tool_name=f"product:{GENERATE_IMAGE_TOOL_NAME}",
        config_version=int(row.config_version),
        tool_label="图片生成",
        definition_sha256=definition_sha256,
        endpoint_url=str(row.endpoint_url),
        conversation_id=conversation_id,
        user_id=user_id,
    )


def parse_generate_image_payload(payload: Any) -> GeneratedImageOutcome:
    """优先读 image-service 的结构化结果；旧版本只有文本时按文本协议解析。"""
    structured = payload.get("structuredContent") if isinstance(payload, dict) else None
    if not isinstance(structured, dict) or not isinstance(structured.get("ok"), bool):
        image_path, model = parse_generate_image_text(_payload_text(payload))
        return GeneratedImageOutcome(image_path=image_path, model=model)
    if not structured["ok"]:
        error = structured.get("error") if isinstance(structured.get("error"), dict) else {}
        code = error.get("code")
        fallback_from = error.get("fallback_from")
        fallback_code = fallback_from.get("code") if isinstance(fallback_from, dict) else None
        raise ImageGenerationError(
            code if code in KNOWN_ERROR_CODES else "upstream_generation_failed",
            retryable=error.get("retryable") is True,
            fallback_from=fallback_code if fallback_code in KNOWN_ERROR_CODES else None,
        )
    image_path = _image_path(structured.get("image_url"))
    return GeneratedImageOutcome(
        image_path=image_path,
        model=_model_id(structured.get("model")),
        requested_model=_model_id(structured.get("requested_model")),
        fallback_used=structured.get("fallback_used") is True,
    )


def _image_path(url: Any) -> str:
    if not isinstance(url, str):
        raise ImageGenerationError("invalid_response")
    try:
        path = urlsplit(url).path
    except ValueError:
        raise ImageGenerationError("invalid_response") from None
    if not _IMAGE_PATH.fullmatch(path):
        raise ImageGenerationError("invalid_response")
    return path


def _model_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _MODEL_ID.fullmatch(value) else None


def parse_generate_image_text(text: str) -> tuple[str, str | None]:
    """从 image-service 返回文本取出图片路径与模型；只接受该服务静态目录下的图片文件。"""

    if text.lstrip().startswith(_ERROR_PREFIX):
        raise ImageGenerationError("upstream_generation_failed")
    match = _GENERATED_LINE.search(text)
    if match is None:
        raise ImageGenerationError("invalid_response")
    path = _image_path(match.group(1))
    model_match = _MODEL_LINE.search(text)
    return path, model_match.group(1) if model_match else None


def internal_image_url(endpoint_url: str, image_path: str) -> str:
    """图片按 MCP 端点的内网源站下载；返回文本里的主机名不保证在容器内可解析。"""

    endpoint = urlsplit(endpoint_url)
    return f"{endpoint.scheme}://{endpoint.netloc}{image_path}"


class ImageGenerationToolHandler(BaseToolHandler):
    # 生图有费用且非幂等：不自动重试，也不做运行内成功复用。
    supports_automatic_retry = False
    execution_timeout_seconds = EXECUTION_TIMEOUT_SECONDS

    def __init__(
        self,
        *,
        binding: ImageServiceToolBinding,
        remote_executor: Any,
        remote_definition_sha256: str,
        store_image: StoreImageFn,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        max_images_per_run: int = MAX_IMAGES_PER_RUN,
    ) -> None:
        self.binding = binding
        self.remote_executor = remote_executor
        self.remote_definition_sha256 = remote_definition_sha256
        self.store_image = store_image
        self.http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)
        )
        self.max_images_per_run = max_images_per_run
        self._attempts = 0
        self._lock = asyncio.Lock()

    @property
    def tool_name(self) -> str:
        return self.binding.alias

    @property
    def sse_event_prefix(self) -> str:
        return "mcp"

    def validate_arguments(self, args: dict) -> list[dict[str, str]]:
        try:
            _normalized_arguments(args)
        except ImageGenerationError:
            return [{"field": "request", "code": "invalid_arguments"}]
        return []

    async def execute(self, args: dict) -> ToolResult:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        try:
            arguments = _normalized_arguments(args)
        except ImageGenerationError as error:
            return self._failed(error.code, started_at)
        async with self._lock:
            if self._attempts >= self.max_images_per_run:
                return self._failed("run_image_limit_reached", started_at)
            self._attempts += 1
        try:
            payload = await self.remote_executor.call(
                IMAGE_SERVICE_REMOTE_TOOL_NAME,
                self.remote_definition_sha256,
                arguments,
                min_call_timeout_seconds=MCP_CALL_TIMEOUT_SECONDS,
            )
            outcome = parse_generate_image_payload(payload)
            content, mime_type = await self._download(outcome.image_path)
            stored = await self.store_image(
                content=content,
                mime_type=mime_type,
                user_id=self.binding.user_id,
                conversation_id=self.binding.conversation_id,
                filename=outcome.image_path.rsplit("/", 1)[-1],
            )
        except McpClientError as error:
            return self._failed(error.code, started_at)
        except ImageGenerationError as error:
            return self._failed(
                error.code,
                started_at,
                retryable=error.retryable,
                fallback_from=error.fallback_from,
            )
        except ValueError:
            return self._failed("image_store_rejected", started_at)
        except Exception as error:  # noqa: BLE001 — 存储等基础设施故障如实报失败，不中断本轮
            logger.warning("生图结果转存失败: error_type=%s", type(error).__name__)
            return self._failed("image_store_failed", started_at)
        return ToolResult(
            status="success",
            duration_ms=_duration_ms(started_at),
            data={
                **self._metadata(),
                "file_id": stored["file_id"],
                "mime_type": stored["mime_type"],
                "width": stored.get("width"),
                "height": stored.get("height"),
                "prompt": arguments["prompt"],
                "aspect_ratio": arguments["aspect_ratio"],
                "model": outcome.model,
                "requested_model": outcome.requested_model,
                "fallback_used": outcome.fallback_used,
            },
        )

    async def _download(self, image_path: str) -> tuple[bytes, str]:
        url = internal_image_url(self.binding.endpoint_url, image_path)
        try:
            async with self.http_client_factory() as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise ImageGenerationError("image_download_failed")
                    mime_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if mime_type not in _IMAGE_MIME_TYPES:
                        raise ImageGenerationError("image_download_failed")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise ImageGenerationError("image_too_large")
                        chunks.append(chunk)
        except httpx.HTTPError:
            raise ImageGenerationError("image_download_failed") from None
        content = b"".join(chunks)
        if not content:
            raise ImageGenerationError("image_download_failed")
        return content, mime_type

    def _metadata(self) -> dict[str, Any]:
        return {
            "mcp_server_id": self.binding.server_id,
            "remote_tool_name": self.binding.remote_tool_name,
            "provider": self.binding.provider,
            "config_version": self.binding.config_version,
            "definition_sha256": self.binding.definition_sha256,
        }

    def _failed(
        self,
        error_code: str,
        started_at: float,
        *,
        retryable: bool = False,
        fallback_from: str | None = None,
    ) -> ToolResult:
        data = {**self._metadata(), "error_code": error_code, "retryable": retryable}
        if fallback_from:
            data["fallback_from"] = fallback_from
        return ToolResult(
            status="failed",
            duration_ms=_duration_ms(started_at),
            data=data,
            error_message="图片生成失败",
        )

    def build_content_block(self, result: ToolResult, block_id: str, log_id: str) -> GeneratedImageBlock | None:
        if result.status != "success":
            return None
        data = result.data
        return GeneratedImageBlock(
            type="generated_image",
            id=block_id,
            schema_version=1,
            provider=self.binding.provider,
            file_id=data["file_id"],
            mime_type=data["mime_type"],
            width=data.get("width"),
            height=data.get("height"),
            prompt=data["prompt"],
            aspect_ratio=data.get("aspect_ratio"),
            model=data.get("model"),
            tool_call_log_id=log_id,
        )

    def format_llm_context(self, result: ToolResult, *, citation_numbers: list[int] | None = None) -> str:
        del citation_numbers
        data = result.data or {}
        if result.status != "success":
            return render_runtime_prompt(
                "image_service.failed_context",
                error_code=data.get("error_code") or "image_generation_failed",
                retryable=data.get("retryable") is True,
                fallback_from=data.get("fallback_from"),
            )
        return render_runtime_prompt("image_service.success_context")

    def _build_result_summary(self, result: ToolResult) -> dict:
        data = result.data or {}
        summary: dict[str, Any] = {"kind": "generated_image", "truncated": False}
        if result.status == "success":
            summary["file_id"] = data.get("file_id")
        else:
            summary["error_code"] = data.get("error_code")
        return summary


def _normalized_arguments(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"prompt", "aspect_ratio", "style", "regenerate"}:
        raise ImageGenerationError("invalid_arguments")
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise ImageGenerationError("invalid_arguments")
    aspect_ratio = args.get("aspect_ratio", "1:1")
    if aspect_ratio not in ASPECT_RATIOS:
        raise ImageGenerationError("invalid_arguments")
    regenerate = args.get("regenerate", False)
    if not isinstance(regenerate, bool):
        raise ImageGenerationError("invalid_arguments")
    arguments: dict[str, Any] = {
        "prompt": prompt.strip(),
        "aspect_ratio": aspect_ratio,
        "fallback_model": FALLBACK_MODEL,
    }
    if regenerate:
        arguments["no_cache"] = True
    style = args.get("style")
    if style is not None:
        if not isinstance(style, str) or len(style) > MAX_STYLE_CHARS:
            raise ImageGenerationError("invalid_arguments")
        if style.strip():
            arguments["style"] = style.strip()
    return arguments


def _payload_text(payload: Any) -> str:
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        raise ImageGenerationError("invalid_response")
    return "\n".join(item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str))


def _duration_ms(started_at: float) -> int:
    return max(0, int((asyncio.get_running_loop().time() - started_at) * 1_000))
