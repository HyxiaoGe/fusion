import { describe, expect, it } from 'vitest';

import { normalizeAgentRunConfig } from './runConfig';

describe('normalizeAgentRunConfig', () => {
  it('完整映射 run config', () => {
    expect(normalizeAgentRunConfig({
      max_steps: 12,
      max_tool_calls: 30,
      timeout_s: 600,
      plan_mode: 'on',
    })).toEqual({
      maxSteps: 12,
      maxToolCalls: 30,
      timeoutS: 600,
      planMode: 'on',
    });
  });

  it('旧历史和未知值安全归一化', () => {
    expect(normalizeAgentRunConfig({})).toEqual({
      maxSteps: 0,
      maxToolCalls: 0,
      timeoutS: 0,
      planMode: 'auto',
    });
  });

  it('旧深度研究记录的任务字段被忽略，按计划模式还原', () => {
    expect(normalizeAgentRunConfig({
      plan_mode: 'on',
      task_mode: 'deep_research',
      network_profile: 'deep_research',
      evidence_policy: 'deep_research_v1',
    } as Parameters<typeof normalizeAgentRunConfig>[0])).toEqual({
      maxSteps: 0,
      maxToolCalls: 0,
      timeoutS: 0,
      planMode: 'on',
    });
  });

  it('记录了深度思考开关时带上 useReasoning，非布尔值忽略', () => {
    expect(normalizeAgentRunConfig({ use_reasoning: true }).useReasoning).toBe(true);
    expect(normalizeAgentRunConfig({ use_reasoning: false }).useReasoning).toBe(false);
    expect(normalizeAgentRunConfig({ use_reasoning: 'yes' })).not.toHaveProperty('useReasoning');
  });
});
