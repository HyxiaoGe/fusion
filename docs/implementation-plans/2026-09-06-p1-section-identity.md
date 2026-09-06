# P1 稳定 Section Identity 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改 Prompt 正文和 catalog 的前提下，让主聊天输入侧通过不可变 `PromptMessage` 携带稳定 `section_id`，彻底停止用正文子串完成去重、收尾清理和语言契约幂等。

**Architecture:** 新增一个只在进程内存在的不可变消息类型，完整保留 provider 消息字段，但把 `section_id` 留在内部元数据中。系统提示词组装、Agent Run 消息变换与收尾流程始终操作该类型；只有调用 LiteLLM 或 tokenizer 的统一边界函数会生成普通 provider dict，并保证 `section_id` 不外泄。

**Tech Stack:** Python 3.11、`dataclasses`、`collections.abc.Mapping`、FastAPI 服务层、pytest/unittest、Ruff。

**Spec:** `docs/specs/backend/2026-09-05-global-prompt-runtime.md`

## Global Constraints

- 本阶段只实施 P1，不扩 catalog、不英文化 Prompt 正文、不提前实施 P2-P5。
- `PromptMessage(role, content, section_id | None)` 必须是不可变内部类型；非 system 消息的 `section_id` 必须为 `None`。
- `section_id` 不得写入模型可见正文，也不得出现在 provider dict。
- 去重、终局控制契约清理、语言契约幂等必须只依赖身份，不能依赖 Prompt 正文。
- `user_visible_content.py` 的输出侧 best-effort sanitizer 保持原样。
- 能力路由、工具权限、Skill 终态、Trajectory 投影和现有 Agent 行为契约不得回退。

---

### Task 1: 建立不可变 PromptMessage 与 provider 边界

**Files:**
- Create: `backend/app/ai/prompts/prompt_message.py`
- Create: `backend/test/test_prompt_message.py`
- Modify: `backend/app/ai/llm_round_observability.py`

**Interfaces:**
- Produces: `PromptMessage(role: str, content: Any, section_id: str | None, provider_fields: Mapping[str, Any])`
- Produces: `ensure_prompt_message(message, *, section_id=None) -> PromptMessage`
- Produces: `to_provider_messages(messages) -> list[dict[str, Any]]`

- [x] **Step 1: 写失败测试**

```python
def test_prompt_message_is_immutable_and_rejects_non_system_identity():
    message = PromptMessage(role="system", content="规则", section_id="app_identity")
    with pytest.raises(FrozenInstanceError):
        message.content = "变化"
    with pytest.raises(ValueError):
        PromptMessage(role="user", content="问题", section_id="app_identity")

def test_provider_conversion_strips_internal_identity_and_keeps_protocol_fields():
    message = PromptMessage.from_provider_dict(
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]}
    )
    assert to_provider_messages([message]) == [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]}
    ]
```

- [x] **Step 2: 验证 RED**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_prompt_message.py -q`

Expected: 因 `app.ai.prompts.prompt_message` 尚不存在而失败。

- [x] **Step 3: 写最小实现**

实现冻结 dataclass + Mapping 只读接口；`to_provider_messages()` 是唯一允许剥离内部身份的函数。`estimate_prompt_tokens()` 在 tokenizer 外部边界调用该函数。

- [x] **Step 4: 验证 GREEN**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_prompt_message.py test/test_prompt_fingerprint.py -q`

Expected: 全部通过。

### Task 2: 让基础消息组装保留身份

**Files:**
- Modify: `backend/app/ai/prompts/system_prompt.py`
- Modify: `backend/app/services/chat/message_builder.py`
- Modify: `backend/app/services/stream/agent_loop_request_prep.py`
- Modify: `backend/app/services/knowledge/chat_grounding.py`
- Modify: `backend/test/test_system_prompt_assembly.py`
- Modify: `backend/test/services/stream/test_agent_loop_request_prep.py`

**Interfaces:**
- Consumes: `PromptMessage`、`ensure_prompt_message()`
- Produces: `SystemPromptAssembly.messages: list[PromptMessage]`
- Produces: `AgentLoopPreparedMessages.messages: list[PromptMessage]`

- [x] **Step 1: 写失败测试**

```python
def test_assembly_keeps_section_identity_outside_provider_payload():
    result = assemble_system_prompt(sections=lambda: [SystemPromptSection("tool", "任意正文")])
    assert [message.section_id for message in result.messages] == ["app_identity", "tool", "current_date"]
    assert all("section_id" not in dict(message) for message in result.messages)

def test_prepare_messages_keeps_none_identity_for_non_system_messages():
    prepared = await prepare_agent_loop_messages(...)
    assert all(message.section_id is None for message in prepared.messages if message.role != "system")
```

- [x] **Step 2: 验证 RED**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_system_prompt_assembly.py test/services/stream/test_agent_loop_request_prep.py -q`

Expected: 普通 dict 没有 `section_id` 属性，测试失败。

- [x] **Step 3: 写最小实现**

基础段落、动态段落、文件/URL/知识库上下文统一转为 `PromptMessage`；现有 provider 字段和消息顺序不变。已有 `prompt_snapshot.sections` 直接读取消息身份，不再依赖 `zip(metadata.section_ids, messages)`。

- [x] **Step 4: 验证 GREEN**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_system_prompt_assembly.py test/services/stream/test_agent_loop_request_prep.py test/test_system_prompt_assembly.py -q`

Expected: 全部通过。

### Task 3: 去重、语言策略和收尾清理改按身份

**Files:**
- Modify: `backend/app/services/stream/agent_loop_request_prep.py`
- Modify: `backend/app/services/chat/model_call_language_policy.py`
- Modify: `backend/app/services/stream/limit_summary.py`
- Modify: `backend/app/services/stream/agent_loop_round_outcome.py`
- Modify: `backend/app/services/stream/agent_loop_driver.py`
- Modify: `backend/test/services/chat/test_model_call_language_policy.py`
- Modify: `backend/test/services/stream/test_agent_loop_request_prep.py`
- Modify: `backend/test/services/stream/test_limit_summary.py`

**Interfaces:**
- Produces: 稳定的 `tool_usage_contract`、`agent_plan_control`、`deep_research_contract`、`no_tool_network_boundary`、`no_vision_file_boundary`、`visible_response_language` 与终局/修复 section id。
- Produces: `remove_conflicting_tool_usage_contract()` 仅按 section id 删除控制段落。

- [x] **Step 1: 写失败测试**

```python
def test_injector_deduplicates_when_body_changes_but_identity_is_stable():
    messages = [PromptMessage("system", "热更新后的任意正文", "tool_usage_contract")]
    result = inject_tool_usage_contract(messages, web_search_call_kwargs)
    assert [message.section_id for message in result].count("tool_usage_contract") == 1

def test_summary_cleanup_uses_identity_and_does_not_delete_same_body_without_identity():
    removable = PromptMessage("system", "完全改写后的控制正文", "agent_plan_control")
    keep = PromptMessage("system", "【执行计划控制规则】只是普通引用", "user_preferences")
    messages = [removable, keep]
    remove_conflicting_tool_usage_contract(messages)
    assert messages == [keep]
```

语言策略测试先放入正文被任意替换的旧 `visible_response_language` 消息，再验证只剩一个当前身份；不再按 `VISIBLE_RESPONSE_LANGUAGE_PROMPT` 做 `replace()`。

- [x] **Step 2: 验证 RED**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/services/chat/test_model_call_language_policy.py test/services/stream/test_agent_loop_request_prep.py test/services/stream/test_limit_summary.py -q`

Expected: 现有实现仍依赖正文相等或中文 marker，测试失败。

- [x] **Step 3: 写最小实现**

为每个受控 system 消息创建带身份的 `PromptMessage`；终局清理按固定身份集合和 `skill:` 前缀处理；终局 Prompt 与无证据/修复 Prompt 分别使用独立身份。移除 `append_limit_summary_prompt()` 对正文是否包含非披露语句的冗余判断；P0 有效模板与代码默认值本就包含该语句，所以当前模型输入字节不变，后续正文完整性由模板自身负责。

- [x] **Step 4: 验证 GREEN**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/services/chat/test_model_call_language_policy.py test/services/stream/test_agent_loop_request_prep.py test/services/stream/test_limit_summary.py -q`

Expected: 全部通过，且正文任意变化不影响身份行为。

### Task 4: 在真实 LLM 调用边界剥离身份

**Files:**
- Modify: `backend/app/services/stream/agent_round.py`
- Modify: `backend/app/services/stream/limit_summary.py`
- Modify: `backend/app/services/chat_service.py`
- Modify: `backend/test/services/stream/test_agent_round.py`
- Modify: `backend/test/services/stream/test_limit_summary.py`
- Modify: `backend/test/test_chat_service.py`

**Interfaces:**
- Consumes: `to_provider_messages()`
- Guarantees: LiteLLM/tokenizer 收到普通 dict；任何 dict 都不含 `section_id`；内部 ContextPlan 在裁剪前后保留 `PromptMessage` 身份。

- [x] **Step 1: 写失败测试**

```python
async def test_llm_boundary_receives_plain_dict_without_section_identity():
    async def llm_call_fn(_model, _kwargs, messages, **_call_kwargs):
        assert all(type(message) is dict for message in messages)
        assert all("section_id" not in message for message in messages)
        return response()
```

- [x] **Step 2: 验证 RED**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/services/stream/test_agent_round.py test/services/stream/test_limit_summary.py test/test_chat_service.py -q`

Expected: 内部 `PromptMessage` 尚未在外部调用边界统一转换，测试失败。

- [x] **Step 3: 写最小实现**

Agent 普通轮、终局总结和非流式聊天只在发给 LiteLLM 的最后一刻调用 `to_provider_messages()`；Context 管理、去重、过滤和 fingerprint 继续读取内部消息。

- [x] **Step 4: 验证 GREEN**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/services/stream/test_agent_round.py test/services/stream/test_limit_summary.py test/test_chat_service.py -q`

Expected: 全部通过。

### Task 5: 完整回归与交付记录

**Files:**
- Modify: `docs/EXECUTION_LEDGER.md`

- [x] **Step 1: 运行 P1 目标集合**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_prompt_message.py test/test_system_prompt_assembly.py test/services/chat/test_model_call_language_policy.py test/services/stream/test_agent_loop_request_prep.py test/services/stream/test_agent_round.py test/services/stream/test_limit_summary.py test/test_prompt_runtime_templates.py test/test_chat_service.py -q`

Expected: 全部通过。

- [x] **Step 2: 运行共享行为契约**

Run: `DATABASE_URL="sqlite:///:memory:" python -m pytest test/test_agent_behavior_eval.py test/test_prompt_consumer_call_sites.py test/services/agent/test_continuation.py -q`

Expected: 既有能力路由、Prompt 消费和 continuation 契约全部通过。

- [x] **Step 3: 运行静态门禁**

Run: `python -m ruff check app test`

Run: `python -m ruff format --check <本次改动的 Python 文件>`

Run: `git diff --check`

Expected: 全部退出 0。

- [x] **Step 4: 更新执行台账**

记录 P1 的完成层级、目标测试数量、Ruff/diff 结果，以及“尚未 push、PR、CI、dev 或真实模型验收”的准确边界。
