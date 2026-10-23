import { modelFallbackReasonLabel } from '@/lib/agent/modelFallback';
import { planItemStatusLabel } from '@/lib/agent/planState';
import i18n from '@/lib/i18n';
import { getToolMeta, hasToolMeta } from '@/lib/agent/toolRegistry';
import type { AgentPlanItemStatus } from '@/types/agentRun';
import { extractTextFromBlocks, isContentFilteredMessage } from '@/types/conversation';
import type { TrajectoryCell } from './TrajectoryCellProjection';

export interface TrajectoryCellPresentation {
  kindLabel: string;
  summary: string;
  statusLabel: string | null;
  durationMs: number | null;
  tone: 'neutral' | 'info' | 'success' | 'warn' | 'danger';
  trajectoryStatusLabel?: string | null;
  trajectoryTone?: 'neutral' | 'info' | 'success' | 'warn' | 'danger';
  isSkeleton?: boolean;
}

const STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  recording: '记录中',
  completed: '已完成',
  complete: '已完成',
  success: '完成',
  failed: '失败',
  timeout: '超时',
  degraded: '部分可用',
  interrupted: '已中断',
  cancelled: '已取消',
  limit_reached: '已达上限',
  incomplete: '部分完成',
  legacy: '历史未记录',
  unknown: '状态未知',
};

const MAX_CELL_SUMMARY_LENGTH = 160;

export function formatTrajectoryStatus(status: string): string {
  return STATUS_LABELS[status] ?? '状态未知';
}

export function formatTrajectoryDuration(durationMs: number | null): string | null {
  if (durationMs === null || !Number.isFinite(durationMs) || durationMs < 0) return null;
  if (durationMs < 1000) return `${Math.round(durationMs)} 毫秒`;
  const seconds = (durationMs / 1000).toFixed(2).replace(/\.00$/, '').replace(/0$/, '');
  return `${seconds} 秒`;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function boundedSummary(value: string): string {
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (!normalized) return '';
  return normalized.length > MAX_CELL_SUMMARY_LENGTH
    ? `${normalized.slice(0, MAX_CELL_SUMMARY_LENGTH - 1)}…`
    : normalized;
}

function latestDuration(cell: Extract<TrajectoryCell, { type: 'tool' | 'subtool' }>): number | null {
  for (let index = cell.events.length - 1; index >= 0; index -= 1) {
    const duration = finiteNumber(cell.events[index].payload.duration_ms);
    if (duration !== null) return duration;
  }
  return null;
}

function runDuration(cell: Extract<TrajectoryCell, { type: 'run' }>): number | null {
  if (!cell.startedAt || !cell.endedAt) return null;
  const duration = Date.parse(cell.endedAt) - Date.parse(cell.startedAt);
  return Number.isFinite(duration) && duration >= 0 ? duration : null;
}

function trajectoryBadgeLabel(
  status: Extract<TrajectoryCell, { type: 'run' }>['trajectoryBadge']['status'],
): string | null {
  switch (status) {
    case 'recording': return '轨迹记录中';
    // 完整是常态，不占状态列；只标出需要留意的情况。
    case 'complete': return null;
    case 'degraded': return '轨迹降级';
    case 'truncated': return '轨迹已截断';
    case 'legacy': return '历史未记录轨迹';
    case 'summary-only': return '仅运行摘要';
    case 'unknown': return '轨迹状态未知';
  }
}

function trajectoryBadgeTone(
  status: Extract<TrajectoryCell, { type: 'run' }>['trajectoryBadge']['status'],
): TrajectoryCellPresentation['tone'] {
  if (status === 'complete') return 'success';
  if (status === 'recording') return 'info';
  if (status === 'degraded' || status === 'truncated') return 'warn';
  return 'neutral';
}

function contextSummary(cell: Extract<TrajectoryCell, { type: 'context' }>): string {
  if (cell.eventType === 'system_prompt_prepared') {
    return i18n.t(`trajectory.systemPrompt.${cell.payload.status === 'failed' ? 'failed' : 'ready'}`);
  }
  if (cell.eventType === 'context_required') {
    return boundedSummary(stringValue(cell.payload.purpose) ?? '等待补充信息');
  }
  if (cell.eventType === 'context_result') {
    return formatTrajectoryStatus(stringValue(cell.payload.status) ?? 'complete');
  }
  if (cell.eventType === 'context_status_updated') {
    const status = stringValue(cell.payload.status);
    const statusLabel = contextStatusLabel(status);
    const actualTokens = finiteNumber(cell.payload.actual_prompt_tokens);
    const windowTokens = finiteNumber(cell.payload.window_tokens);
    if (stringValue(cell.payload.phase) === 'final' && actualTokens !== null) {
      const actualLabel = actualTokens.toLocaleString('en-US');
      const windowLabel = windowTokens === null ? null : windowTokens.toLocaleString('en-US');
      return `${statusLabel} · 实际 ${actualLabel}${windowLabel ? ` / ${windowLabel}` : ''} Token`;
    }
    return statusLabel;
  }
  return boundedSummary(stringValue(cell.payload.phase)
    ?? stringValue(cell.payload.status)
    ?? '上下文已更新');
}

function contextStatusLabel(status: string | null): string {
  switch (status) {
    case 'no_op_fast_path':
    case 'no_op':
      return '上下文充足';
    case 'trimmed':
    case 'trimmed_required_above_target':
      return '上下文已压缩';
    case 'required_context_over_budget':
      return '上下文超出预算';
    case 'estimator_unavailable':
      return '上下文用量不可用';
    case 'bypass_unknown_window':
      return '上下文窗口未知';
    default:
      return '上下文已更新';
  }
}

export function getTrajectoryCellPresentation(cell: TrajectoryCell): TrajectoryCellPresentation {
  switch (cell.type) {
    case 'user':
      return {
        kindLabel: '用户提问',
        summary: boundedSummary(extractTextFromBlocks(cell.message.content)) || '无文字内容',
        statusLabel: null,
        durationMs: null,
        tone: 'neutral',
      };
    case 'message':
      return {
        kindLabel: '回答',
        summary: isContentFilteredMessage(cell.message.content)
          ? '被模型服务商内容审核拦截，显示固定提示'
          : boundedSummary(extractTextFromBlocks(cell.message.content)) || '回答内容待生成',
        statusLabel: cell.message.status === 'failed' ? '失败' : null,
        durationMs: null,
        tone: cell.message.status === 'failed' ? 'danger' : 'neutral',
      };
    case 'assistant_request':
      return {
        kindLabel: cell.requestIndex === null
          ? '模型请求'
          : `模型请求 #${cell.requestIndex}`,
        summary: llmRoundSummary(cell),
        statusLabel: formatTrajectoryStatus(cell.status),
        durationMs: cell.durationMs,
        tone: statusTone(cell.status),
      };
    case 'run':
      return {
        kindLabel: cell.attemptIndex === null ? '执行' : `第 ${cell.attemptIndex} 次执行`,
        summary: cell.isHydrated
          ? `${cell.totalSteps} 步 · ${cell.totalToolCalls} 次工具`
          : '轨迹详情待加载',
        statusLabel: formatTrajectoryStatus(cell.runStatus),
        durationMs: runDuration(cell),
        tone: statusTone(cell.runStatus),
        trajectoryStatusLabel: trajectoryBadgeLabel(cell.trajectoryBadge.status),
        trajectoryTone: trajectoryBadgeTone(cell.trajectoryBadge.status),
        isSkeleton: !cell.isHydrated,
      };
    case 'plan': {
      const progress = planProgress(trajectoryPlanItems(cell.payload));
      return {
        kindLabel: '计划',
        summary: progress.summary,
        statusLabel: progress.statusLabel,
        durationMs: null,
        tone: progress.tone,
      };
    }
    case 'context':
      return {
        kindLabel: '上下文',
        summary: contextSummary(cell),
        statusLabel: null,
        durationMs: cell.eventType === 'system_prompt_prepared'
          ? finiteNumber(cell.payload.duration_ms)
          : null,
        tone: cell.eventType === 'system_prompt_prepared'
          ? (cell.payload.status === 'failed' ? 'danger' : 'success')
          : 'info',
      };
    case 'tool': {
      const meta = getToolMeta(cell.toolName ?? '');
      return {
        kindLabel: meta.label,
        summary: toolArgumentSummary(cell) || '工具调用',
        statusLabel: formatTrajectoryStatus(cell.status),
        durationMs: latestDuration(cell),
        tone: statusTone(cell.status),
      };
    }
    case 'subtool': {
      const meta = getToolMeta(cell.toolName ?? '');
      return {
        kindLabel: '工具尝试',
        summary: `${meta.label}${cell.attemptIndex === null ? '' : ` · 第 ${cell.attemptIndex} 次`}`,
        statusLabel: formatTrajectoryStatus(cell.status),
        durationMs: latestDuration(cell),
        tone: statusTone(cell.status),
      };
    }
    case 'compacted':
      return {
        kindLabel: '上下文压缩',
        summary: `移除 ${cell.removedTurns} 轮 · ${cell.removedMessages} 条消息 · ${cell.removedToolTransactions} 次工具`,
        statusLabel: null,
        durationMs: null,
        tone: 'neutral',
      };
    case 'model_fallback': {
      const failed = cell.failedModel ?? '原模型';
      const reason = modelFallbackReasonLabel(cell.reason);
      return {
        kindLabel: '换模型',
        summary: cell.fallbackModel
          ? `${failed} → ${cell.fallbackModel} · ${reason}`
          : `${failed} 没有可换的备用模型，限时重试 · ${reason}`,
        statusLabel: cell.fallbackModel ? '已切换' : '重试原模型',
        durationMs: null,
        tone: 'warn',
      };
    }
  }
}

export interface TrajectoryPlanItem {
  id: string;
  title: string;
  status: AgentPlanItemStatus;
  statusLabel: string;
  summary: string | null;
}

const PLAN_ITEM_STATUSES = new Set<string>(['pending', 'running', 'completed', 'failed', 'skipped', 'blocked']);

/** 计划快照里的步骤，按模型给出的顺序。 */
export function trajectoryPlanItems(payload: Record<string, unknown>): TrajectoryPlanItem[] {
  if (!Array.isArray(payload.items)) return [];
  return payload.items.flatMap((item: unknown, index) => {
    if (!item || typeof item !== 'object') return [];
    const record = item as Record<string, unknown>;
    const title = stringValue(record.title);
    if (!title) return [];
    const status = (PLAN_ITEM_STATUSES.has(String(record.status)) ? record.status : 'pending') as AgentPlanItemStatus;
    return [{
      id: stringValue(record.id) ?? `item-${index}`,
      title,
      status,
      statusLabel: planItemStatusLabel(status),
      summary: stringValue(record.summary),
    }];
  });
}

function planProgress(items: readonly TrajectoryPlanItem[]): {
  summary: string;
  statusLabel: string | null;
  tone: TrajectoryCellPresentation['tone'];
} {
  if (items.length === 0) return { summary: '计划没有步骤', statusLabel: null, tone: 'neutral' };
  const settled = items.filter(item => item.status === 'completed' || item.status === 'skipped').length;
  const running = items.find(item => item.status === 'running');
  const failed = items.some(item => item.status === 'failed');
  const blocked = items.some(item => item.status === 'blocked');
  const parts = [`${items.length} 个步骤`];
  if (settled === items.length) parts.push('全部完成');
  else {
    parts.push(`完成 ${settled}/${items.length}`);
    if (running) parts.push(`进行中：${running.title}`);
  }
  const summary = boundedSummary(parts.join(' · '));
  if (failed) return { summary, statusLabel: '有步骤失败', tone: 'danger' };
  if (blocked) return { summary, statusLabel: '有步骤受阻', tone: 'warn' };
  if (settled === items.length) return { summary, statusLabel: '已完成', tone: 'success' };
  if (running) return { summary, statusLabel: '进行中', tone: 'info' };
  return { summary, statusLabel: '待执行', tone: 'neutral' };
}

/** 工具显示名：登记过的用中文名，MCP 等外部工具用原名。 */
export function trajectoryToolLabel(toolName: string | null): string {
  if (!toolName) return '工具';
  return hasToolMeta(toolName) ? getToolMeta(toolName).label : toolName;
}

/** 工具行的参数摘要（搜索词、网址等）；参数未记录时为空串。 */
export function toolArgumentSummary(cell: Extract<TrajectoryCell, { type: 'tool' }>): string {
  if (!cell.arguments || !cell.toolName) return '';
  return boundedSummary(getToolMeta(cell.toolName).summarize(cell.arguments));
}

/**
 * 模型轮次先说这一轮做了什么（调了哪些工具，或输出了回答），再接思考预览。
 * 不接正文预览：最后一轮的正文就是下一行的回答，重复显示没有信息量。
 */
function llmRoundSummary(cell: Extract<TrajectoryCell, { type: 'assistant_request' }>): string {
  return boundedSummary([llmRoundAction(cell), cell.reasoningPreview].filter(Boolean).join(' · '));
}

function llmRoundAction(cell: Extract<TrajectoryCell, { type: 'assistant_request' }>): string {
  if (cell.toolNames.length > 0) {
    const counts = new Map<string, number>();
    for (const name of cell.toolNames) {
      const label = trajectoryToolLabel(name);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    const parts = [...counts].map(([label, count]) => (count > 1 ? `${label}×${count}` : label));
    const tools = `调用 ${cell.toolNames.length} 个工具：${parts.join('、')}`;
    return cell.updatedPlan ? `更新计划，${tools}` : tools;
  }
  if (cell.updatedPlan) return '更新计划';
  if (cell.status === 'running') return '生成中';
  if (cell.status === 'success' || cell.status === 'completed') return '输出回答';
  return '未完成';
}

function statusTone(status: string): TrajectoryCellPresentation['tone'] {
  if (status === 'failed' || status === 'timeout') return 'danger';
  if (status === 'degraded' || status === 'limit_reached' || status === 'incomplete') return 'warn';
  if (status === 'running' || status === 'recording') return 'info';
  if (status === 'completed' || status === 'complete' || status === 'success') return 'success';
  return 'neutral';
}
