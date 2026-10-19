import type { AgentRunConfig } from '@/types/agentRun';

interface WireRunConfig {
  max_steps?: unknown;
  max_tool_calls?: unknown;
  timeout_s?: unknown;
  use_reasoning?: unknown;
}

function nonNegativeNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0;
}

export function normalizeAgentRunConfig(config: WireRunConfig | null | undefined): AgentRunConfig {
  const source = config ?? {};

  return {
    maxSteps: nonNegativeNumber(source.max_steps),
    maxToolCalls: nonNegativeNumber(source.max_tool_calls),
    timeoutS: nonNegativeNumber(source.timeout_s),
    ...(typeof source.use_reasoning === 'boolean' ? { useReasoning: source.use_reasoning } : {}),
  };
}
