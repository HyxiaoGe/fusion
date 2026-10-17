"""测试用工具基类：补齐 BaseToolHandler 的抽象成员，子类只覆盖测试关心的部分。"""

from app.services.tool_handlers.base import BaseToolHandler, ToolResult, ToolRuntimeContext


class FakeToolHandler(BaseToolHandler):
    # 普通类属性覆盖抽象 property，子类可直接写 tool_name = "x" 或在 __init__ 里赋值。
    tool_name = "fake_tool"
    sse_event_prefix = "fake_tool"

    async def execute(self, args: dict, context: ToolRuntimeContext | None = None) -> ToolResult:
        raise NotImplementedError

    def build_content_block(self, result, block_id, log_id):
        return None

    def format_llm_context(self, result, *, citation_numbers=None):
        return str(result.data)


def fake_tool_handler(**attrs) -> FakeToolHandler:
    """按关键字参数覆盖实例属性（如 execute=AsyncMock(...)），其余沿用基类默认。"""

    handler = FakeToolHandler()
    for name, value in attrs.items():
        setattr(handler, name, value)
    return handler
