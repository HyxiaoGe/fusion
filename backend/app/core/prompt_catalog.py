"""Fusion 与 PromptHub 之间的固定 Prompt 映射契约。

catalog 是单一事实源：这里声明的每一个 `PromptSpec` 都必须有真实注入路径消费它。
消费方通过 `register_prompt_consumer` 注册 accessor，启动期由
`assert_catalog_fully_consumed()` 校验，缺失即 fail-fast——避免出现
「PromptHub 发布成功、revision 变更，但模型行为不变」的静默空转。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# catalog 结构版本。新增/删除 spec 或改变契约字段时递增。
# code-default effective_revision 的 canonical 摘要以它承担版本身份。
CATALOG_VERSION = "2026-09-05.1"


@dataclass(frozen=True)
class PromptSpec:
    key: str
    slug: str
    name: str
    variables: tuple[str, ...]
    marker: str


class PromptCatalogIntegrityError(RuntimeError):
    """catalog 声明与真实消费路径不一致。"""


PROMPT_SPECS = (
    PromptSpec("app_identity", "app-identity", "Fusion 应用身份", (), "【Fusion 身份一致性规则】"),
    PromptSpec("tool_usage_contract", "tool-usage-contract", "工具调用一致性规则", (), "【工具调用一致性规则】"),
    PromptSpec("no_tool_network_boundary", "no-tool-network-boundary", "无联网工具边界", (), "【无联网工具边界规则】"),
    PromptSpec(
        "no_vision_file_boundary", "no-vision-file-boundary", "无图片理解能力边界", (), "【无图片理解能力边界规则】"
    ),
    PromptSpec("url_read_tool_description", "url-read-tool-description", "URL 读取工具说明", (), "读取指定 URL"),
    PromptSpec("limit_summary", "limit-summary", "工具上限总结", (), "工具调用上限"),
    PromptSpec("continuation_system", "continuation-system", "续写系统规则", (), "继续上一轮"),
    PromptSpec("generate_title", "generate-title", "生成会话标题", ("content",), "对话内容："),
    PromptSpec(
        "generate_suggested_questions",
        "generate-suggested-questions",
        "生成推荐问题",
        ("content",),
        "三个推荐问题",
    ),
    PromptSpec("file_analysis", "file-analysis", "文件分析", ("query", "file_content"), "问题:"),
    PromptSpec(
        "file_content_enhancement",
        "file-content-enhancement",
        "文件内容增强",
        ("query", "file_content"),
        "以下是相关文件内容，请结合这些内容回答：",
    ),
)

PROMPT_SPEC_BY_KEY = {spec.key: spec for spec in PROMPT_SPECS}
PROMPT_SPEC_BY_SLUG = {spec.slug: spec for spec in PROMPT_SPECS}

PromptAccessor = Callable[[], str]
_CONSUMERS: dict[str, PromptAccessor] = {}


def register_prompt_consumer(key: str) -> Callable[[PromptAccessor], PromptAccessor]:
    """把真实注入路径的 accessor 注册到 catalog，供启动期完整性校验。"""

    if key not in PROMPT_SPEC_BY_KEY:
        raise PromptCatalogIntegrityError(f"未知 catalog key: {key}")

    def decorator(accessor: PromptAccessor) -> PromptAccessor:
        registered = _CONSUMERS.get(key)
        if registered is not None and registered is not accessor:
            raise PromptCatalogIntegrityError(f"catalog key 重复注册消费方: {key}")
        _CONSUMERS[key] = accessor
        return accessor

    return decorator


def registered_prompt_consumers() -> dict[str, PromptAccessor]:
    """返回已注册的消费方 accessor 副本，供校验与测试使用。"""

    return dict(_CONSUMERS)


def assert_catalog_fully_consumed() -> None:
    """启动期校验：catalog 每一项都必须有真实消费路径，否则 fail-fast。"""

    missing = sorted(set(PROMPT_SPEC_BY_KEY) - set(_CONSUMERS))
    if missing:
        raise PromptCatalogIntegrityError("以下 catalog Prompt 没有注册消费方，热更新会静默空转: " + ", ".join(missing))
