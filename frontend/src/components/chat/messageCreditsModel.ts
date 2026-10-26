import type { MessageCredits } from '@/lib/api/credits';
import { getToolMeta, hasToolMeta } from '@/lib/agent/toolRegistry';

export interface MessageCreditsModelRow {
  model: string;
  rounds: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  credits: number;
}

export interface MessageCreditsToolRow {
  label: string;
  count: number;
  credits: number;
}

export interface MessageCreditsSummary {
  credits: number;
  modelCredits: number;
  toolCredits: number;
  models: MessageCreditsModelRow[];
  tools: MessageCreditsToolRow[];
  /** 这条回答经过几次生成（重新生成、继续生成各算一次） */
  runs: number;
  /** 出错结束、没扣 credits 的生成次数 */
  uncharged: number;
  pending: number;
  offPeak: boolean;
}

function toolLabel(tool: string, label: string): string {
  // MCP 工具的展示名由后端给出（服务名 / 工具名）；内置工具用前端注册表的中文名
  if (tool.startsWith('mcp:')) return label;
  return hasToolMeta(tool) ? getToolMeta(tool).label : label;
}

/** 汇总一条回答各次生成的消耗；出错没扣的生成不计入金额，只计数。 */
export function summarizeMessageCredits(data: MessageCredits): MessageCreditsSummary {
  const models = new Map<string, MessageCreditsModelRow>();
  const tools = new Map<string, MessageCreditsToolRow>();
  let modelCredits = 0;
  let toolCredits = 0;
  let uncharged = 0;
  let offPeak = false;
  for (const entry of data.entries) {
    if (entry.kind !== 'run') continue;
    if (!entry.charged) {
      uncharged += 1;
      continue;
    }
    for (const round of entry.breakdown?.rounds ?? []) {
      const row = models.get(round.model) ?? {
        model: round.model, rounds: 0, inputTokens: 0, cacheReadTokens: 0, outputTokens: 0, credits: 0,
      };
      row.rounds += 1;
      row.inputTokens += round.input_tokens;
      row.cacheReadTokens += round.cache_read_tokens;
      row.outputTokens += round.output_tokens;
      row.credits += round.credits;
      models.set(round.model, row);
      modelCredits += round.credits;
      offPeak ||= round.off_peak;
    }
    for (const tool of entry.breakdown?.tools ?? []) {
      if (tool.credits <= 0) continue;
      const label = toolLabel(tool.tool, tool.label);
      const row = tools.get(label) ?? { label, count: 0, credits: 0 };
      row.count += tool.count;
      row.credits += tool.credits;
      tools.set(label, row);
      toolCredits += tool.credits;
    }
  }
  return {
    credits: data.credits,
    modelCredits,
    toolCredits,
    models: [...models.values()],
    tools: [...tools.values()].sort((a, b) => b.credits - a.credits),
    runs: data.entries.filter((entry) => entry.kind === 'run').length,
    uncharged,
    pending: data.pending_runs,
    offPeak,
  };
}

export function formatTokens(value: number): string {
  if (value >= 10_000) return `${(value / 1000).toFixed(0)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  return String(value);
}
