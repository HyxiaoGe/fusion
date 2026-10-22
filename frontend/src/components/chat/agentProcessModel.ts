import { RUN_STATUS_TREATMENT } from '@/lib/agent/statusTreatment';
import { getToolErrorDisplay } from '@/lib/agent/toolErrorDisplay';
import { historyStepId } from '@/lib/chat/conversationHydration';
import { toLocalTime } from '@/lib/chat/serverClock';
import type { AgentModelFallback, AgentRunState, ToolCallState } from '@/types/agentRun';
import type { ContentBlock } from '@/types/conversation';
import type { TrajectoryRunSummary } from '@/types/trajectory';

/** 过程区里的一行：一段思考、一次工具调用，或一次换备用模型。 */
export type AgentProcessRow =
  | { kind: 'thinking'; id: string; text: string }
  | { kind: 'tool'; id: string; call: ToolCallState }
  | { kind: 'fallback'; id: string; fallback: AgentModelFallback };

/**
 * 按发生顺序排出过程行。工具行只依赖运行步骤，没有思考内容时照样列出。
 *
 * 生成中每段思考挂在所属步骤上，直接按步骤排；历史步骤不带内容块，
 * 服务端按轮次顺序写入思考块，第 k 段思考对应第 k 步。
 */
export function buildAgentProcessRows(
  blocks: readonly ContentBlock[],
  run: AgentRunState,
): AgentProcessRow[] {
  const thinking = blocks.filter(
    (block): block is Extract<ContentBlock, { type: 'thinking' }> => block.type === 'thinking' && Boolean(block.thinking.trim()),
  );
  const fallbacksByStep = new Map<string | null, AgentModelFallback[]>();
  for (const fallback of run.modelFallbacks ?? []) {
    const list = fallbacksByStep.get(fallback.stepId) ?? [];
    list.push(fallback);
    fallbacksByStep.set(fallback.stepId, list);
  }

  const rows: AgentProcessRow[] = [];
  const pushFallbacks = (stepId: string | null) => {
    (fallbacksByStep.get(stepId) ?? []).forEach((fallback, index) => {
      rows.push({ kind: 'fallback', id: `fallback:${stepId ?? 'run'}:${index}`, fallback });
    });
    fallbacksByStep.delete(stepId);
  };
  const pushTools = (calls: readonly ToolCallState[]) => {
    for (const call of calls) rows.push({ kind: 'tool', id: call.toolCallId, call });
  };
  const pushThinking = (block: Extract<ContentBlock, { type: 'thinking' }>) => {
    rows.push({ kind: 'thinking', id: block.id, text: block.thinking });
  };

  const covered = new Set(run.steps.flatMap(step => step.contentBlockIds));
  if (run.steps.length > 0 && thinking.every(block => covered.has(block.id))) {
    const byId = new Map(thinking.map(block => [block.id, block]));
    for (const step of run.steps) {
      pushFallbacks(step.stepId);
      for (const blockId of step.contentBlockIds) {
        const block = byId.get(blockId);
        if (block) pushThinking(block);
      }
      pushTools(step.toolCalls);
    }
  } else {
    const stepsByNumber = new Map(run.steps.map(step => [step.stepNumber, step]));
    const lastStep = Math.max(thinking.length, ...run.steps.map(step => step.stepNumber));
    for (let number = 1; number <= lastStep; number += 1) {
      const step = stepsByNumber.get(number);
      // 历史只返回有工具调用的步骤；没有工具的步骤上的换模型记录按合成 id 对上。
      pushFallbacks(step?.stepId ?? historyStepId(run.runId, number));
      const block = thinking[number - 1];
      if (block) pushThinking(block);
      if (step) pushTools(step.toolCalls);
    }
  }
  // 没对上步骤的换模型记录（如步骤事件晚到）放在末尾，不丢。
  for (const stepId of [...fallbacksByStep.keys()]) pushFallbacks(stepId);
  return rows;
}

export function toolCallDurationMs(call: ToolCallState): number | null {
  if (typeof call.completedAt !== 'number') return null;
  const duration = call.completedAt - call.startedAt;
  return Number.isFinite(duration) && duration >= 0 ? duration : null;
}

export function isAwaitingChoice(call: ToolCallState): boolean {
  return call.resultSummary?.repair_state === 'awaiting_choice';
}

export function runStatusLabel(run: AgentRunState): string {
  if (run.status === 'running') {
    if (run.stopConfirmation) return run.stopConfirmation.status === 'pending' ? '正在确认停止' : '停止结果未确认';
    return '处理中';
  }
  return RUN_STATUS_TREATMENT[run.status].label;
}

function boundedIssue(value: string | undefined): string | null {
  if (!value) return null;
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (!normalized) return null;
  return normalized.length > 120 ? `${normalized.slice(0, 117)}...` : normalized;
}

/** 整轮标题上只放一条最要紧的问题；地名待确认不是故障，不算。 */
export function highestPriorityIssue(run: AgentRunState): string | null {
  const runFailure = boundedIssue(run.failure?.message);
  if (runFailure) return runFailure;

  const calls = run.steps.flatMap(step => step.toolCalls);
  // 只说失败了几次：用某个工具的失败原因当整轮问题，模型重试成功后会读成整件事失败了；具体原因在对应行里。
  const failedCount = calls.filter(call => call.status === 'failed').length;
  if (failedCount > 0) return `${failedCount} 次调用失败`;
  if (run.steps.some(step => step.status === 'failed')) return '执行步骤失败';

  const degraded = calls.find(call => call.status === 'degraded' && !isAwaitingChoice(call));
  if (degraded) {
    return boundedIssue(getToolErrorDisplay(degraded.toolName, degraded.status, degraded.error)) ?? '部分工具结果不可用';
  }
  if (run.status === 'limit_reached') {
    return run.limitReachedReason === 'output_length' ? '回答达到长度上限，已截断' : '执行已达到上限';
  }
  if (run.status === 'incomplete') return '任务仅部分完成';
  return null;
}

export function runDurationMs(run: AgentRunState, now: number, summary?: TrajectoryRunSummary): number | null {
  const ownSummary = summary?.run_id === run.runId ? summary : undefined;
  // 历史消息的步骤没有绝对时间，优先复用同一 Run 的已加载摘要；起止都是服务端时间才可相减。
  if (ownSummary) {
    if (typeof ownSummary.duration_ms === 'number' && Number.isFinite(ownSummary.duration_ms) && ownSummary.duration_ms >= 0) {
      return ownSummary.duration_ms;
    }
    const startedAt = Date.parse(ownSummary.started_at);
    const endedAt = ownSummary.ended_at ? Date.parse(ownSummary.ended_at) : NaN;
    if (Number.isFinite(startedAt) && Number.isFinite(endedAt) && endedAt >= startedAt) {
      return endedAt - startedAt;
    }
  }

  // 历史步骤的起点记为 0，不是真实时间，算不出整轮耗时。
  const starts = run.steps
    .filter(step => Number.isFinite(step.startedAt) && step.startedAt > 0);
  // 步骤起点是事件的服务端时刻换成的本机时间，可以直接对本机 now 计时。
  // 只有中途刷新、本机没见到第 1 步时，才退回摘要里的服务端起点，同样先换成本机时间。
  if (run.status === 'running' && !starts.some(step => step.stepNumber === 1) && ownSummary) {
    const startedAt = Date.parse(ownSummary.started_at);
    if (Number.isFinite(startedAt)) return Math.max(0, now - toLocalTime(startedAt));
  }
  if (starts.length === 0) return null;

  const startedAt = Math.min(...starts.map(step => step.startedAt));
  const completed = run.steps
    .map(step => step.completedAt)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const endedAt = run.status === 'running'
    ? now
    : completed.length > 0
      ? Math.max(...completed)
      : null;
  return endedAt === null ? null : Math.max(0, endedAt - startedAt);
}

/** 思考行收起时只预览第一句。 */
export function firstSentence(text: string, limit = 80): string {
  const normalized = text.replace(/[#*_`>]/g, '').replace(/\s+/g, ' ').trim();
  const match = /^.+?(?:[。！？!?]|\.(?=\s|$))/.exec(normalized);
  const sentence = match ? match[0] : normalized;
  return sentence.length > limit ? `${sentence.slice(0, limit - 1)}…` : sentence;
}
