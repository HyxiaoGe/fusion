import type { AgentRunConfig } from '@/types/agentRun';

interface WireRunConfig {
  max_steps?: unknown;
  max_tool_calls?: unknown;
  timeout_s?: unknown;
  plan_mode?: unknown;
  use_reasoning?: unknown;
}

function nonNegativeNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
}

export function normalizeAgentRunConfig(config: WireRunConfig | null | undefined): AgentRunConfig {
  const source = config ?? {};
  const planMode = source.plan_mode === 'on' || source.plan_mode === 'off'
    ? source.plan_mode
    : 'auto';

  return {
    maxSteps: nonNegativeNumber(source.max_steps),
    maxToolCalls: nonNegativeNumber(source.max_tool_calls),
    timeoutS: nonNegativeNumber(source.timeout_s),
    planMode,
    ...(typeof source.use_reasoning === 'boolean' ? { useReasoning: source.use_reasoning } : {}),
  };
}
