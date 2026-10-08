"""模型服务商内容审核拦截：识别与熔断。

只认服务商给出的协议信号，不按内容语义猜：
- 标准流式终止原因 ``finish_reason=content_filter``（可能发生在回答中途）；
- LiteLLM 的 ContentPolicyViolationError，或经代理后只剩错误消息里的服务商错误码；
- 服务商把拦截当成正常回答返回时的固定原文，必须与整条正文完全相等。

一旦识别，整条回复替换为 ContentFilteredBlock：前端只显示固定提示、不提供重新生成，
这一轮也不进入后续对话上下文。不换模型重试——换一家绕过审核不是兜底该做的事。
"""

from __future__ import annotations

from typing import Any

from app.schemas.chat import ContentFilteredBlock

CONTENT_FILTER_FINISH_REASON = "content_filter"

# 服务商以正常回答（finish_reason=stop）返回的拦截原文。
_REFUSAL_REPLIES = frozenset(
    {
        # 小米 MiMo：输入审核拦截，0 输出 token、无思考
        "The request was rejected because it was considered high risk",
    }
)

# 经 LiteLLM 代理转发后结构化 code 会丢失，只剩错误消息里的服务商错误码。
_PROVIDER_ERROR_CODES = (
    # 阿里通义 DashScope：InternalError.Algo.DataInspectionFailed / data_inspection_failed
    "DataInspectionFailed",
    "data_inspection_failed",
)


def is_refusal_reply(*, content: str, reasoning: str, tool_calls: list[Any]) -> bool:
    return not tool_calls and not reasoning.strip() and content.strip() in _REFUSAL_REPLIES


def is_content_filter_error(error: BaseException) -> bool:
    if type(error).__name__ == "ContentPolicyViolationError":
        return True
    if getattr(error, "status_code", None) != 400:
        return False
    message = str(error)
    return any(code in message for code in _PROVIDER_ERROR_CODES)


async def replace_with_content_filtered_block(*, content_blocks: list[Any], emitter: Any) -> ContentFilteredBlock:
    """整条回复只保留拦截块；已流式显示的思考、正文与工具结果由前端按此块整体隐藏。"""

    block = ContentFilteredBlock(type="content_filtered", schema_version=1)
    content_blocks[:] = [block]
    upsert = getattr(emitter, "content_block_upserted", None)
    if upsert is not None:
        await upsert(tool_call_id=None, content_block=block)
    return block
