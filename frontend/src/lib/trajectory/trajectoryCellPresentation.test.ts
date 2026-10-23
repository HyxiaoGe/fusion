import { describe, expect, it } from 'vitest';

import type { TrajectoryCell } from './TrajectoryCellProjection';
import { getTrajectoryCellPresentation } from './trajectoryCellPresentation';

function runCell(attemptIndex: number | null): Extract<TrajectoryCell, { type: 'run' }> {
  return {
    key: 'run:run-1',
    type: 'run',
    runId: 'run-1',
    userMessageId: 'user-1',
    assistantMessageId: 'assistant-1',
    completenessSources: ['run-summary'],
    sourceSequences: [],
    summarySource: 'run-summary',
    attemptIndex,
    runStatus: 'completed',
    totalSteps: 2,
    totalToolCalls: 1,
    startedAt: '2026-08-26T00:00:00.000Z',
    endedAt: '2026-08-26T00:00:01.000Z',
    isSelected: false,
    isHydrated: true,
    association: 'explicit',
    trajectoryBadge: { status: 'complete', source: 'run-summary', reason: null },
    records: [],
    spans: [],
    liveTail: [],
  };
}

function attemptCell(attemptIndex: number | null): Extract<TrajectoryCell, { type: 'subtool' }> {
  return {
    key: 'run:run-1:subtool:attempt-1',
    type: 'subtool',
    runId: 'run-1',
    userMessageId: 'user-1',
    assistantMessageId: 'assistant-1',
    completenessSources: ['durable-snapshot'],
    sourceSequences: [3],
    toolCallId: 'tool-1',
    toolAttemptId: 'attempt-1',
    toolName: 'web_search',
    toolLabel: null,
    llmRoundKey: null,
    attemptIndex,
    status: 'success',
    events: [],
  };
}

function contextCell(payload: Record<string, unknown>): Extract<TrajectoryCell, { type: 'context' }> {
  return {
    key: 'run:run-1:context:context_status_updated:1',
    type: 'context',
    turnMessageId: 'message-1',
    source: 'durable-snapshot',
    sequenceStart: 2,
    sequenceEnd: 3,
    timestampStart: '2026-08-25T11:43:40.000Z',
    timestampEnd: '2026-08-25T11:43:43.000Z',
    runId: 'run-1',
    contextId: 'context_status_updated:1',
    eventType: 'context_status_updated',
    payload,
  };
}

describe('轨迹单元格展示', () => {
  it.each([
    { attemptIndex: 1, expected: '第 1 次执行' },
    { attemptIndex: 2, expected: '第 2 次执行' },
    { attemptIndex: null, expected: '执行' },
  ])('Run 摘要使用后端序号 $attemptIndex，未知时不补序号', ({ attemptIndex, expected }) => {
    expect(getTrajectoryCellPresentation(runCell(attemptIndex)).kindLabel).toBe(expected);
  });

  it.each([
    { attemptIndex: 1, expected: '搜索 · 第 1 次' },
    { attemptIndex: 2, expected: '搜索 · 第 2 次' },
    { attemptIndex: null, expected: '搜索' },
  ])('工具 Attempt 摘要使用后端序号 $attemptIndex，未知时不补序号', ({ attemptIndex, expected }) => {
    expect(getTrajectoryCellPresentation(attemptCell(attemptIndex)).summary).toBe(expected);
  });

  it('把 final 上下文协议阶段展示为实际 Token 用量而不是内部枚举值', () => {
    const presentation = getTrajectoryCellPresentation(contextCell({
      phase: 'final',
      status: 'no_op_fast_path',
      actual_prompt_tokens: 8459,
      window_tokens: 1_000_000,
    }));

    expect(presentation.summary).toBe('上下文充足 · 实际 8,459 / 1,000,000 Token');
    expect(presentation.summary).not.toContain('final');
  });

  it('被服务商内容审核拦截的回答行写明拦截，而不是显示待生成', () => {
    const cell = {
      key: 'message:assistant-1',
      type: 'message',
      runId: null,
      userMessageId: null,
      assistantMessageId: 'assistant-1',
      completenessSources: ['message'],
      sourceSequences: [],
      message: {
        id: 'assistant-1',
        role: 'assistant',
        content: [{ type: 'content_filtered', id: 'blk_filtered', schema_version: 1 }],
        timestamp: 1,
      },
    } as Extract<TrajectoryCell, { type: 'message' }>;

    expect(getTrajectoryCellPresentation(cell).summary).toBe('被模型服务商内容审核拦截，显示固定提示');
  });

  it.each([
    [['completed', 'running', 'pending'], '3 个步骤 · 完成 1/3 · 进行中：第 2 步', '进行中', 'info'],
    [['completed', 'skipped'], '2 个步骤 · 全部完成', '已完成', 'success'],
    [['completed', 'failed'], '2 个步骤 · 完成 1/2', '有步骤失败', 'danger'],
    [['completed', 'pending'], '2 个步骤 · 完成 1/2', '进行中', 'info'],
    [['blocked', 'pending'], '2 个步骤 · 完成 0/2', '有步骤受阻', 'warn'],
    [['pending'], '1 个步骤 · 完成 0/1', '待执行', 'neutral'],
  ] as const)('计划行按步骤状态汇总进度 %j', (statuses, summary, statusLabel, tone) => {
    const presentation = getTrajectoryCellPresentation({
      key: 'run:run-1:plan:plan-1:2', type: 'plan', runId: 'run-1', userMessageId: 'user-1', assistantMessageId: 'assistant-1',
      completenessSources: ['durable-snapshot'], sourceSequences: [2], planId: 'plan-1', revision: 1, llmRoundKey: null, superseded: false,
      payload: { items: statuses.map((status, index) => ({ id: `s${index}`, title: `第 ${index + 1} 步`, status })) },
    });
    expect(presentation).toMatchObject({ kindLabel: '计划', summary, statusLabel, tone });
  });

  it('被后续版本取代的计划不再显示当时的步骤进度', () => {
    const presentation = getTrajectoryCellPresentation({
      key: 'run:run-1:plan:plan-1:2', type: 'plan', runId: 'run-1', userMessageId: 'user-1', assistantMessageId: 'assistant-1',
      completenessSources: ['durable-snapshot'], sourceSequences: [2], planId: 'plan-1', revision: 1, llmRoundKey: null, superseded: true,
      payload: { items: [{ id: 'a', title: '确定路线', status: 'running' }, { id: 'b', title: '查询天气', status: 'pending' }] },
    });
    expect(presentation).toMatchObject({ kindLabel: '计划', summary: '2 个步骤', statusLabel: '已更新', tone: 'neutral' });
  });

  it('MCP 工具用服务端给的可读名，内置工具仍用登记的中文名', () => {
    const tool = (toolName: string, toolLabel: string | null) => getTrajectoryCellPresentation({
      key: `tool:${toolName}`, type: 'tool', runId: 'run-1', userMessageId: 'user-1', assistantMessageId: 'assistant-1',
      completenessSources: ['durable-snapshot'], sourceSequences: [1], toolCallId: 'call-1', stepId: 'step-1',
      toolName, toolLabel, arguments: null, llmRoundKey: null, status: 'success', events: [],
    });
    expect(tool('mcp_KjRUUI0x', '腾讯地图 / placeSearchNearby').kindLabel).toBe('腾讯地图 / placeSearchNearby');
    expect(tool('web_search', '不该覆盖').kindLabel).toBe('搜索');
    expect(tool('mcp_KjRUUI0x', null).kindLabel).toBe('mcp_KjRUUI0x');
  });
});
