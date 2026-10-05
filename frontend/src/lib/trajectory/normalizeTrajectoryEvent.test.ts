import { describe, expect, it } from 'vitest';

import {
  normalizeTrajectoryCapabilityResolution,
  normalizeSseTrajectoryEvent,
  normalizeTrajectoryRecord,
} from './normalizeTrajectoryEvent';

const timestamp = '2026-08-22T00:00:00.000Z';

const capabilityResolution = {
  schema_version: 3,
  router_version: '2026-10-05.1',
  package_id: 'agent',
  reason_codes: ['all_available_tools'],
  external_tool_names: ['weather_forecast'],
  deferred_tool_names: [],
  effective_plan_mode: 'off',
  network_boundary_required: false,
  bundle_fingerprint: `sha256:${'a'.repeat(64)}`,
};

const legacyResolutionV2 = {
  schema_version: 2,
  router_version: '2026-08-31.1',
  package_id: 'verified_web',
  confidence: 'high',
  resolution_mode: 'routed',
  reason_codes: ['verified_source_request'],
  external_tool_names: ['web_search', 'url_read'],
  effective_plan_mode: 'auto',
  include_current_date: true,
  network_boundary_required: false,
  bundle_fingerprint: `sha256:${'c'.repeat(64)}`,
};

describe('normalizeTrajectoryEvent', () => {
  it('实时与历史 run_started 只保留完整合法的能力路由对象', () => {
    const live = normalizeSseTrajectoryEvent({
      type: 'run_started',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 0,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      tools: ['weather_forecast'],
      capability_resolution: capabilityResolution,
    });
    const durable = normalizeTrajectoryRecord('run-1', {
      sequence: 0,
      event_type: 'run_started',
      schema_version: 1,
      timestamp,
      step_id: null,
      tool_call_id: null,
      parent_step_id: null,
      trace_id: 'trace-1',
      payload: {
        type: 'run_started',
        run_id: 'run-1',
        tools: ['weather_forecast'],
        capability_resolution: capabilityResolution,
      },
    });

    expect(live?.payload.capability_resolution).toEqual(capabilityResolution);
    expect(durable?.payload.capability_resolution).toEqual(capabilityResolution);
    expect(live?.payload.capability_resolution).not.toBe(capabilityResolution);
    expect(durable).toEqual(live);
  });

  it('实时 config 与历史布尔都只保留动态发现已开启，并丢弃授权目录', () => {
    const catalog = ['weather_forecast', 'web_search'];
    const live = normalizeSseTrajectoryEvent({
      type: 'run_started',
      schema_version: 1,
      run_id: 'run-discovery',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 0,
      trace_id: 'trace-discovery',
      ts: Date.parse(timestamp) / 1000,
      tools: [],
      capability_resolution: null,
      config: {
        dynamic_tool_discovery: {
          enabled: true,
          authorized_tool_names: catalog,
        },
      },
    });
    const durable = normalizeTrajectoryRecord('run-discovery', {
      sequence: 0,
      event_type: 'run_started',
      schema_version: 1,
      timestamp,
      step_id: null,
      tool_call_id: null,
      parent_step_id: null,
      trace_id: 'trace-discovery',
      payload: {
        type: 'run_started',
        run_id: 'run-discovery',
        dynamic_tool_discovery_enabled: true,
        config: {
          dynamic_tool_discovery: {
            enabled: true,
            authorized_tool_names: catalog,
          },
        },
      },
    });

    expect(live?.payload.dynamic_tool_discovery_enabled).toBe(true);
    expect(durable?.payload.dynamic_tool_discovery_enabled).toBe(true);
    expect(JSON.stringify(live?.payload)).not.toContain('authorized_tool_names');
    expect(JSON.stringify(live?.payload)).not.toContain('weather_forecast');
    expect(JSON.stringify(durable?.payload)).not.toContain('weather_forecast');
    expect(normalizeSseTrajectoryEvent({
      type: 'run_started',
      schema_version: 1,
      run_id: 'run-off',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 0,
      trace_id: 'trace-off',
      ts: Date.parse(timestamp) / 1000,
      config: { dynamic_tool_discovery: { enabled: false, authorized_tool_names: catalog } },
    })?.payload.dynamic_tool_discovery_enabled).toBeUndefined();
  });

  it('旧 v1/v2 能力路由读侧视为未记录，只丢弃能力对象，不丢弃 run_started', () => {
    const legacyV1 = { ...legacyResolutionV2, schema_version: 1 };
    for (const legacy of [legacyV1, legacyResolutionV2]) {
      const live = normalizeSseTrajectoryEvent({
        type: 'run_started',
        schema_version: 1,
        run_id: 'run-legacy',
        parent_run_id: null,
        step_id: null,
        parent_step_id: null,
        tool_call_id: null,
        sequence: 0,
        trace_id: 'trace-legacy',
        ts: Date.parse(timestamp) / 1000,
        tools: ['web_search', 'url_read'],
        capability_resolution: legacy,
      });
      const durable = normalizeTrajectoryRecord('run-legacy', {
        sequence: 0,
        event_type: 'run_started',
        schema_version: 1,
        timestamp,
        step_id: null,
        tool_call_id: null,
        parent_step_id: null,
        trace_id: 'trace-legacy',
        payload: {
          type: 'run_started',
          run_id: 'run-legacy',
          tools: ['web_search', 'url_read'],
          capability_resolution: legacy,
        },
      });

      expect(live).not.toBeNull();
      expect(live?.payload).not.toHaveProperty('capability_resolution');
      expect(durable).toEqual(live);
      expect(normalizeTrajectoryCapabilityResolution(legacy)).toBeNull();
    }
  });

  it('旧版能力升级事件不再属于公共事件，读回时整条丢弃', () => {
    expect(normalizeTrajectoryRecord('run-legacy', {
      sequence: 4, event_type: 'capability_escalated', schema_version: 1, timestamp, step_id: null,
      parent_step_id: null, tool_call_id: null, trace_id: 'trace-legacy',
      payload: { protocol_version: 2, step_number: 1, from_package_id: 'direct', capability_resolution: legacyResolutionV2 },
    })).toBeNull();
  });

  it('保留按需检索工具列表', () => {
    const resolution = { ...capabilityResolution, deferred_tool_names: ['mcp_docs_search', 'mcp_calendar_lookup'] };
    expect(normalizeTrajectoryCapabilityResolution(resolution)).toEqual(resolution);
  });

  it.each([
    ['额外字段', { ...capabilityResolution, raw_query: '北京天气' }],
    ['旧版置信度字段', { ...capabilityResolution, confidence: 'high' }],
    ['缺少按需检索列表', Object.fromEntries(Object.entries(capabilityResolution).filter(([key]) => key !== 'deferred_tool_names'))],
    ['控制工具', { ...capabilityResolution, external_tool_names: ['update_plan'] }],
    ['按需列表含控制工具', { ...capabilityResolution, deferred_tool_names: ['update_plan'] }],
    ['超界工具', { ...capabilityResolution, external_tool_names: Array.from({ length: 129 }, (_, index) => `tool_${index}`) }],
    ['重复理由', { ...capabilityResolution, reason_codes: ['all_available_tools', 'all_available_tools'] }],
    ['超界理由', { ...capabilityResolution, reason_codes: ['all_available_tools', 'tools_disabled', 'deep_research_mode'] }],
    ['已删除的 auto 计划', { ...capabilityResolution, effective_plan_mode: 'auto' }],
    ['非法版本', { ...capabilityResolution, router_version: 'latest' }],
    ['非法指纹', { ...capabilityResolution, bundle_fingerprint: 'a'.repeat(64) }],
  ])('拒绝%s的能力路由对象', (_label, value) => {
    expect(normalizeTrajectoryCapabilityResolution(value)).toBeNull();
  });

  it.each([
    ['不可用模式仍公告工具', {
      ...capabilityResolution,
      package_id: 'tools_unavailable',
      reason_codes: ['tools_disabled'],
      external_tool_names: ['web_search'],
    }],
    ['深度研究公告产品工具', { ...capabilityResolution, package_id: 'deep_research', reason_codes: ['deep_research_mode'] }],
    ['有工具却要求联网边界', { ...capabilityResolution, network_boundary_required: true }],
  ])('不再由 UI 判定后端字段的跨字段语义：%s', (_label, value) => {
    // 模式与工具、联网边界的一致性由后端契约保证；UI 复制一份判定只会在后端调整时静默丢字段（issue #26）。
    expect(normalizeTrajectoryCapabilityResolution(value)).not.toBeNull();
    const event = normalizeSseTrajectoryEvent({
      type: 'run_started', schema_version: 1, run_id: 'run-cross-field',
      parent_run_id: null, step_id: null, parent_step_id: null, tool_call_id: null,
      sequence: 0, trace_id: 'trace-cross-field', ts: Date.parse(timestamp) / 1000,
      tools: value.external_tool_names,
      capability_resolution: value,
    });
    expect(event?.payload).toHaveProperty('capability_resolution');
  });

  it('未知模式降级展示而不是丢弃整条 resolution', () => {
    const resolution = {
      ...capabilityResolution,
      package_id: 'future_mode',
      reason_codes: ['future_reason_code'],
    };

    expect(normalizeTrajectoryCapabilityResolution(resolution)).toEqual(resolution);
  });

  it('未知标识符仍受形状约束', () => {
    expect(normalizeTrajectoryCapabilityResolution({
      ...capabilityResolution,
      package_id: 'Not A Mode Id',
    })).toBeNull();
    expect(normalizeTrajectoryCapabilityResolution({
      ...capabilityResolution,
      reason_codes: ['不是标识符'],
    })).toBeNull();
    expect(normalizeTrajectoryCapabilityResolution({
      ...capabilityResolution,
      package_id: 'x'.repeat(64),
    })).toBeNull();
  });

  it.each([
    ['agent', {}],
    ['agent_with_deferred_tools', { external_tool_names: ['web_search', 'url_read'], deferred_tool_names: ['mcp_docs_search'] }],
    ['deep_research', { package_id: 'deep_research', reason_codes: ['deep_research_mode'], external_tool_names: ['web_search', 'url_read'], effective_plan_mode: 'on' }],
    ['knowledge_grounded', { package_id: 'knowledge_grounded', reason_codes: ['knowledge_grounded_mode'], external_tool_names: [], network_boundary_required: true }],
    ['tools_unavailable', { package_id: 'tools_unavailable', reason_codes: ['tools_disabled'], external_tool_names: [], network_boundary_required: true }],
  ])('接受 API 契约中的合法 %s 模式', (_modeId, overrides) => {
    const resolution = { ...capabilityResolution, ...overrides };
    expect(normalizeTrajectoryCapabilityResolution(resolution)).toEqual(resolution);
  });

  it('缺失 schema_version 的已知 SSE 事件按 legacy 版本归一化', () => {
    const tools = ['weather'];
    const normalized = normalizeSseTrajectoryEvent({
      type: 'run_started',
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 0,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      conversation_id: 'conversation-1',
      message_id: 'message-1',
      task_id: 'task-1',
      model: 'deepseek-chat',
      tools,
    });

    expect(normalized).toEqual({
      runId: 'run-1',
      sequence: 0,
      eventType: 'run_started',
      schemaVersion: 0,
      timestamp,
      stepId: null,
      toolCallId: null,
      parentStepId: null,
      traceId: 'trace-1',
      payload: {
        conversation_id: 'conversation-1',
        message_id: 'message-1',
        task_id: 'task-1',
        model: 'deepseek-chat',
        tools: ['weather'],
      },
    });
    expect(normalized?.payload.tools).not.toBe(tools);
  });

  it('拒绝未知 schema 或不在公共 union 的 SSE 事件', () => {
    const baseEvent = {
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 1,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
    };

    expect(normalizeSseTrajectoryEvent({
      ...baseEvent,
      type: 'run_started',
      schema_version: 2,
      conversation_id: 'conversation-1',
      message_id: 'message-1',
      task_id: 'task-1',
      model: 'deepseek-chat',
      tools: [],
    })).toBeNull();
    expect(normalizeSseTrajectoryEvent({ ...baseEvent, type: 'internal_debug_event' })).toBeNull();
  });

  it('只保留事件类型 allowlist 字段且不保留原 payload 引用', () => {
    const payload = {
      tool_name: 'weather',
      status: 'success',
      duration_ms: 120,
      plan_item_id: 'plan-1',
      arguments: { api_key: 'secret' },
      internal_note: '不可进入普通用户轨迹',
    };

    const normalized = normalizeSseTrajectoryEvent({
      type: 'tool_call_completed',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: 'step-1',
      parent_step_id: null,
      tool_call_id: 'tool-1',
      sequence: 2,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      ...payload,
    });

    expect(normalized?.payload).toEqual({
      tool_name: 'weather',
      status: 'success',
      duration_ms: 120,
      plan_item_id: 'plan-1',
    });
    expect(normalized?.payload).not.toBe(payload);
  });

  it('保留 LLM 完成事件的推理 Token，同时丢弃未声明字段', () => {
    const normalized = normalizeSseTrajectoryEvent({
      type: 'llm_round_completed',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: 'step-1',
      parent_step_id: null,
      tool_call_id: null,
      sequence: 3,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      llm_round_id: 'round-1',
      status: 'success',
      input_tokens: 100,
      output_tokens: 40,
      reasoning_tokens: 24,
      duration_ms: 800,
      internal_usage_detail: { secret: true },
    });

    expect(normalized?.payload).toMatchObject({
      llm_round_id: 'round-1',
      status: 'success',
      input_tokens: 100,
      output_tokens: 40,
      reasoning_tokens: 24,
      duration_ms: 800,
    });
    expect(normalized?.payload).not.toHaveProperty('internal_usage_detail');
  });

  it('对 plan_snapshot 的嵌套项应用字段白名单、脱敏和有界列表', () => {
    const normalized = normalizeSseTrajectoryEvent({
      type: 'plan_snapshot',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 3,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      protocol_version: 2,
      plan_id: 'plan-1',
      items: Array.from({ length: 51 }, (_, index) => ({
        id: `item-${index}`,
        title: 'api_key=live-secret',
        summary: '长文本'.repeat(300),
        tool_names: Array.from({ length: 51 }, (_, toolIndex) => `token=tool-${toolIndex}`),
        arguments: { api_key: '不能进入 UI' },
        raw_tool_output: '不能进入 UI',
      })),
    });

    const items = normalized?.payload.items as Array<Record<string, unknown>>;
    expect(items).toHaveLength(50);
    expect(Object.keys(items[0]).sort()).toEqual(['id', 'summary', 'title', 'tool_names']);
    expect(items[0].title).toBe('api_key=[REDACTED]');
    expect(items[0].summary).toHaveLength(512);
    expect(items[0].tool_names).toEqual(Array(50).fill('token=[REDACTED]'));
  });

  it('对 evidence URL、嵌套未知字段和文本 secret 应用普通用户安全边界', () => {
    const normalized = normalizeSseTrajectoryEvent({
      type: 'evidence_item_upserted',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 4,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      protocol_version: 2,
      evidence: {
        id: 'evidence-1',
        title: 'authorization: Bearer live-token',
        url: 'https://example.com/guide?access_token=live-token#section',
        raw_tool_output: '不能进入 UI',
      },
    });

    expect(normalized?.payload.evidence).toEqual({
      id: 'evidence-1',
      title: 'authorization=[REDACTED]',
      url: 'https://example.com/guide',
    });
  });

  it('将 P1 record 与相同 SSE 事件归一为同一普通用户事件', () => {
    const record = {
      sequence: 2,
      event_type: 'tool_call_completed',
      schema_version: 1,
      timestamp,
      step_id: 'step-1',
      tool_call_id: 'tool-1',
      parent_step_id: null,
      trace_id: 'trace-1',
      span_id: 'tool:tool-1',
      payload: {
        type: 'tool_call_completed',
        run_id: 'run-1',
        tool_name: 'weather',
        status: 'success',
        duration_ms: 120,
        plan_item_id: 'plan-1',
        arguments: { api_key: 'secret' },
      },
    };
    const sse = {
      type: 'tool_call_completed',
      schema_version: 1,
      run_id: 'run-1',
      parent_run_id: null,
      step_id: 'step-1',
      parent_step_id: null,
      tool_call_id: 'tool-1',
      sequence: 2,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      tool_name: 'weather',
      status: 'success',
      duration_ms: 120,
      plan_item_id: 'plan-1',
      arguments: { api_key: 'secret' },
    };

    expect(normalizeTrajectoryRecord('run-1', record)).toEqual(normalizeSseTrajectoryEvent(sse));
  });

  it.each([
    ['UTC 整秒', '2026-08-22T00:00:00Z', '2026-08-22T00:00:00.000Z'],
    ['六位微秒', '2026-08-22T00:00:00.123456Z', '2026-08-22T00:00:00.123Z'],
    ['带 offset', '2026-08-22T08:00:00.123+08:00', '2026-08-22T00:00:00.123Z'],
  ])('把真实 P1 %s timestamp 归一为 UTC 毫秒 ISO', (_label, durableTimestamp, expected) => {
    const normalized = normalizeTrajectoryRecord('run-1', {
      sequence: 1,
      event_type: 'step_started',
      schema_version: 1,
      timestamp: durableTimestamp,
      step_id: 'step-1',
      tool_call_id: null,
      parent_step_id: null,
      trace_id: 'trace-1',
      span_id: 'step:step-1',
      payload: {
        type: 'step_started',
        run_id: 'run-1',
        step_number: 1,
      },
    });

    expect(normalized?.timestamp).toBe(expected);
  });

  it.each([
    ['非时间字符串', 'not-a-timestamp'],
    ['不存在的日期', '2026-02-30T00:00:00Z'],
  ])('拒绝%s的真实 P1 timestamp，不回退到当前时间', (_label, timestamp) => {
    expect(normalizeTrajectoryRecord('run-1', {
      sequence: 1,
      event_type: 'step_started',
      schema_version: 1,
      timestamp,
      step_id: 'step-1',
      tool_call_id: null,
      parent_step_id: null,
      trace_id: 'trace-1',
      span_id: 'step:step-1',
      payload: {
        type: 'step_started',
        run_id: 'run-1',
        step_number: 1,
      },
    })).toBeNull();
  });

  it('将 P1 schema_version=0 record 与缺失版本的 SSE 归一为相同 legacy 事件', () => {
    const record = {
      sequence: 5,
      event_type: 'run_completed',
      schema_version: 0,
      timestamp,
      step_id: null,
      tool_call_id: null,
      parent_step_id: null,
      trace_id: 'trace-1',
      span_id: null,
      payload: {
        type: 'run_completed',
        run_id: 'run-1',
        total_steps: 2,
        total_tool_calls: 1,
        finish_reason: 'stop',
      },
    };
    const sse = {
      type: 'run_completed',
      run_id: 'run-1',
      parent_run_id: null,
      step_id: null,
      parent_step_id: null,
      tool_call_id: null,
      sequence: 5,
      trace_id: 'trace-1',
      ts: Date.parse(timestamp) / 1000,
      total_steps: 2,
      total_tool_calls: 1,
      finish_reason: 'stop',
    };

    expect(normalizeTrajectoryRecord('run-1', record)).toEqual(normalizeSseTrajectoryEvent(sse));
    expect(normalizeSseTrajectoryEvent(sse)?.schemaVersion).toBe(0);
  });
});


describe('系统提示词元数据', () => {
  const base = { run_id: 'run-1', step_id: null, parent_step_id: null, tool_call_id: null, sequence: 1, trace_id: 'trace-1', ts: Date.parse(timestamp) / 1000 };
  it.each([['ready', 'available'], ['ready', 'degraded'], ['failed', null]])('实时与历史保留 %s/%s 安全元数据', (status, detail_status) => {
    const payload = { protocol_version: 2, status, detail_status, source: 'code', template_version: 'v1', section_ids: ['base'], fingerprint: 'a'.repeat(64), char_count: 123, duration_ms: 0 };
    const live = normalizeSseTrajectoryEvent({ ...base, type: 'system_prompt_prepared', ...payload, prompt: '私密偏好', sections: [{ section_id: 'user_preferences', content: '私密偏好' }] });
    expect(live?.payload).toEqual(payload);
    expect(normalizeTrajectoryRecord('run-1', { sequence: 1, event_type: 'system_prompt_prepared', timestamp, step_id: null, parent_step_id: null, tool_call_id: null, trace_id: 'trace-1', payload })).toEqual(live);
  });
  it.each(['preparing', 'unknown'])('不接受虚构状态 %s', status => {
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'system_prompt_prepared', protocol_version: 2, status, source: 'code', template_version: 'v1', section_ids: [], duration_ms: 0 })).toBeNull();
  });
  it('保留后端实际固定失败文案', () => {
    const message = '系统提示词组装失败，请稍后重试。';
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'system_prompt_prepared', protocol_version: 2, status: 'failed', source: 'code', template_version: 'v1', section_ids: [], duration_ms: 1, message })?.payload.message).toBe(message);
  });
  it('失败允许缺失或空元数据，不保留异常原文或非法指纹', () => {
    const payload = { protocol_version: 2, status: 'failed', source: 'code', template_version: 'v1', section_ids: [], duration_ms: 1 };
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'system_prompt_prepared', ...payload, fingerprint: '私密内容', char_count: -1, message: '原始用户偏好', detail_status: '私密偏好' })?.payload).toEqual(payload);
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'system_prompt_prepared', ...payload, fingerprint: null, char_count: null, message: null })?.payload).toEqual({ ...payload, fingerprint: null, char_count: null, message: null });
  });
  it('保留本次模型请求指纹，旧事件不补填', () => {
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'llm_round_started', llm_round_id: 'r', system_prompt_fingerprint: 'b'.repeat(64) })?.payload.system_prompt_fingerprint).toBe('b'.repeat(64));
    expect(normalizeSseTrajectoryEvent({ ...base, type: 'llm_round_started', llm_round_id: 'old' })?.payload.system_prompt_fingerprint).toBeUndefined();
  });
});

describe('正文归因的实时与历史安全投影', () => {
  it.each(['llm_round_completed', 'llm_round_failed', 'llm_round_cancelled'])('%s 保留有界归因并丢弃全文', type => {
    const provenance = { disposition: 'replaced', source: 'server', reason: 'product_guard', block_id: 'text-1' };
    const payload = { llm_round_id: 'round-1', output_provenance: { ...provenance, candidate: '禁止复制', answer: '禁止复制' } };
    const live = normalizeSseTrajectoryEvent({
      type, schema_version: 1, run_id: 'run-1', step_id: 'step-1', sequence: 3,
      trace_id: 'trace-1', tool_call_id: null, parent_step_id: null,
      ts: Date.parse(timestamp) / 1000, ...payload,
    });
    const history = normalizeTrajectoryRecord('run-1', {
      record_type: 'event', event_type: type, run_id: 'run-1', schema_version: 1,
      sequence: 3, timestamp, step_id: 'step-1', tool_call_id: null, parent_step_id: null, trace_id: null, payload,
    });
    expect(live?.payload.output_provenance).toEqual(provenance);
    expect(history?.payload.output_provenance).toEqual(provenance);
    expect(JSON.stringify(live)).not.toContain('禁止复制');
  });
});


it('保留三个主工具和两个联网替代工具的运行能力信息', () => {
  const value = { ...capabilityResolution, external_tool_names: ['web_search', 'url_read', 'route_compare', 'search_flights', 'search_trains'] };
  expect(normalizeTrajectoryCapabilityResolution(value)?.external_tool_names).toEqual(value.external_tool_names);
});

describe('工具上下文可见性', () => {
  it('实时与历史保留有界 ID 集合并拒绝混入正文', () => {
    const context_visibility = { schema_version: 1, scope: 'application_messages_after_context_management', context_status: 'trimmed', before_tool_call_ids: ['old', 'kept'], visible_tool_call_ids: ['kept'], removed_tool_call_ids: ['old'], before_count: 2, visible_count: 1, removed_count: 1, truncated: false };
    const envelope = { type: 'llm_round_started', run_id: 'run-v', sequence: 2, trace_id: 'trace-v', ts: 100, llm_round_id: 'round-v', schema_version: 1, step_id: null, tool_call_id: null, parent_step_id: null };
    const live = normalizeSseTrajectoryEvent({ ...envelope, context_visibility });
    const durable = normalizeTrajectoryRecord('run-v', { sequence: 2, event_type: 'llm_round_started', schema_version: 1, timestamp, step_id: null, parent_step_id: null, tool_call_id: null, trace_id: 'trace-v', payload: { ...envelope, context_visibility } });
    expect(live?.payload.context_visibility).toEqual(context_visibility);
    expect(durable?.payload.context_visibility).toEqual(context_visibility);
    expect(normalizeSseTrajectoryEvent({ ...envelope, context_visibility: { ...context_visibility, visible_tool_call_ids: ['token=secret'] } })?.payload.context_visibility).toBeNull();
    expect(normalizeSseTrajectoryEvent({ ...envelope, context_visibility: { ...context_visibility, visible_tool_call_ids: Array.from({ length: 201 }, () => 'id') } })?.payload.context_visibility).toBeNull();
  });
});
