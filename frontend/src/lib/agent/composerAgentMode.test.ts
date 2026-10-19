import { describe, expect, it } from 'vitest';

import {
  composerAgentModeFromRunConfig,
  getComposerAgentModeAvailability,
  resolveComposerAgentMode,
} from './composerAgentMode';

describe('composerAgentMode', () => {
  it.each([
    ['auto', 'auto'],
    ['plan', 'on'],
  ] as const)('把 %s 映射为 plan_mode=%s', (requestedMode, planMode) => {
    expect(resolveComposerAgentMode(requestedMode, {
      functionCalling: true,
      searchCapable: true,
      agentTools: true,
    })).toEqual({
      requestedMode,
      effectiveMode: requestedMode,
      planMode,
    });
  });

  it('计划只要求工具调用，不要求联网工具', () => {
    expect(getComposerAgentModeAvailability('plan', {
      functionCalling: true,
      searchCapable: false,
      agentTools: false,
    })).toEqual({ enabled: true });
  });

  it('模型不支持工具调用时安全回退自动模式', () => {
    expect(resolveComposerAgentMode('plan', {
      functionCalling: false,
      searchCapable: true,
    })).toEqual({
      requestedMode: 'plan',
      effectiveMode: 'auto',
      planMode: 'auto',
      fallbackReason: '当前模型不支持工具调用',
    });
  });

  it('按运行配置还原执行模式，计划开启即规划执行', () => {
    expect(composerAgentModeFromRunConfig({ planMode: 'on' })).toBe('plan');
    expect(composerAgentModeFromRunConfig({ planMode: 'auto' })).toBe('auto');
    expect(composerAgentModeFromRunConfig({})).toBe('auto');
  });
});
