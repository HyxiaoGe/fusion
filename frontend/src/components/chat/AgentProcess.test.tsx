import React, { act } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';

import type { AgentRunState, AgentStepState, ToolCallState } from '@/types/agentRun';
import type { ContentBlock } from '@/types/conversation';
import type { TrajectoryRunSummary } from '@/types/trajectory';
import AgentProcess from './AgentProcess';
import { buildAgentProcessRows } from './agentProcessModel';

function call(overrides: Partial<ToolCallState> = {}): ToolCallState {
  return {
    toolCallId: 'tool-1',
    toolName: 'web_search',
    arguments: { query: '杭州天气' },
    status: 'success',
    startedAt: 1_000,
    completedAt: 1_800,
    ...overrides,
  };
}

function step(stepNumber: number, overrides: Partial<AgentStepState> = {}): AgentStepState {
  return {
    stepId: `step-${stepNumber}`,
    stepNumber,
    status: 'completed',
    toolCalls: [],
    contentBlockIds: [],
    startedAt: 1_000,
    completedAt: 2_500,
    ...overrides,
  };
}

function run(overrides: Partial<AgentRunState> = {}): AgentRunState {
  return {
    runId: 'run-1',
    messageId: 'assistant-1',
    status: 'completed',
    config: { maxSteps: 8, maxToolCalls: 16, timeoutS: 300 },
    totalSteps: 1,
    totalToolCalls: 0,
    steps: [step(1)],
    lastSequence: 4,
    ...overrides,
  };
}

function summary(overrides: Partial<TrajectoryRunSummary> = {}): TrajectoryRunSummary {
  return {
    run_id: 'run-1', message_id: 'assistant-1', turn_message_id: 'user-1',
    attempt_index: 1, status: 'completed', trajectory_status: 'complete',
    total_steps: 4, total_tool_calls: 2, duration_ms: 19_038,
    started_at: '2026-08-26T02:27:58.000Z', ended_at: '2026-08-26T02:28:17.038Z',
    llm_detail_schema_version: 1, llm_round_count: 4,
    ...overrides,
  };
}

function thinking(id: string, text: string): ContentBlock {
  return { type: 'thinking', id, thinking: text };
}

function renderProcess(agentRun: AgentRunState, props: Partial<React.ComponentProps<typeof AgentProcess>> = {}) {
  return render(
    <AgentProcess
      run={agentRun}
      rows={buildAgentProcessRows([], agentRun)}
      thinkingStreaming={false}
      isVisible={false}
      onToggle={vi.fn()}
      trajectoryStatus="complete"
      {...props}
    />,
  );
}

describe('buildAgentProcessRows', () => {
  it('生成中按步骤挂的思考排列，工具紧跟所属步骤的思考', () => {
    const agentRun = run({
      steps: [
        step(1, { contentBlockIds: ['t1'], toolCalls: [call({ toolCallId: 'a' }), call({ toolCallId: 'b' })] }),
        step(2, { contentBlockIds: ['t2'] }),
      ],
    });
    const rows = buildAgentProcessRows([thinking('t2', '第二段'), thinking('t1', '第一段')], agentRun);
    expect(rows.map(row => row.id)).toEqual(['t1', 'a', 'b', 't2']);
  });

  it('历史步骤不带内容块时第 k 段思考对应第 k 步，没有思考的工具也照样列出', () => {
    const agentRun = run({
      steps: [
        step(1, { toolCalls: [call({ toolCallId: 'a' })] }),
        step(3, { toolCalls: [call({ toolCallId: 'c' })] }),
      ],
    });
    const blocks = [thinking('t1', '一'), thinking('t2', '二'), thinking('t3', '三'), thinking('t4', '四')];
    expect(buildAgentProcessRows(blocks, agentRun).map(row => row.id)).toEqual(['t1', 'a', 't2', 't3', 'c', 't4']);
    expect(buildAgentProcessRows([], agentRun).map(row => row.id)).toEqual(['a', 'c']);
  });

  it('换模型记录排在所属步骤开头，对不上步骤的放在末尾', () => {
    const agentRun = run({
      steps: [step(1, { toolCalls: [call({ toolCallId: 'a' })] })],
      modelFallbacks: [
        { stepId: 'step-1', failedModel: 'mimo', fallbackModel: 'deepseek-chat', reason: 'first_output_timeout' },
        { stepId: 'step-9', failedModel: 'deepseek-chat', fallbackModel: null, reason: 'http_503' },
      ],
    });
    expect(buildAgentProcessRows([], agentRun).map(row => row.kind)).toEqual(['fallback', 'tool', 'fallback']);
  });
});

describe('AgentProcess', () => {
  beforeEach(async () => { await i18n.changeLanguage('zh-CN'); });
  afterEach(() => vi.useRealTimers());

  it('整轮标题汇总状态、耗时与调用次数，收起时不渲染过程行内容', () => {
    const onInspect = vi.fn();
    const agentRun = run({ steps: [step(1, { toolCalls: [call()] })] });
    renderProcess(agentRun, { onInspect });

    const process = screen.getByRole('region', { name: 'Agent 运行过程' });
    const toggle = within(process).getByRole('button', { name: /已完成/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveTextContent('用时 1.5 秒 · 1 次工具调用');
    expect(process).not.toHaveTextContent('轨迹完整');

    fireEvent.click(within(process).getByRole('button', { name: '查看轨迹' }));
    expect(onInspect).toHaveBeenCalledTimes(1);
  });

  it('展开后工具行显示参数摘要与耗时，点开可看参数和结果', () => {
    const agentRun = run({
      steps: [step(1, {
        toolCalls: [call({ resultSummary: { kind: 'search', count: 6, truncated: false } })],
      })],
    });
    renderProcess(agentRun, { isVisible: true });

    const row = screen.getByRole('button', { name: /搜索杭州天气/ });
    expect(row).toHaveTextContent('800 毫秒');
    fireEvent.click(row);
    expect(screen.getByText('query')).toBeInTheDocument();
    expect(screen.getByText('6 条结果')).toBeInTheDocument();
  });

  it('失败显示原因，地名待确认不算故障也不进标题', () => {
    const agentRun = run({
      steps: [step(1, {
        toolCalls: [
          call({
            toolCallId: 'w',
            toolName: 'weather_forecast',
            arguments: { location: '朝阳' },
            status: 'degraded',
            resultSummary: { kind: 'tool', truncated: false, repair_state: 'awaiting_choice' },
          }),
        ],
      })],
    });
    const view = renderProcess(agentRun, { isVisible: true });
    expect(screen.getByText('待确认地点')).toBeInTheDocument();
    expect(screen.queryByText(/部分工具结果/)).toBeNull();

    const failed = run({
      status: 'failed',
      failure: { code: 'provider_down', message: '模型服务暂时不可用' },
      steps: [step(1, { toolCalls: [call({ status: 'failed', error: '次级工具异常' })] })],
    });
    view.rerender(
      <AgentProcess run={failed} rows={buildAgentProcessRows([], failed)} thinkingStreaming={false} isVisible onToggle={vi.fn()} trajectoryStatus="degraded" />,
    );
    const header = screen.getByRole('button', { name: /^失败/ });
    expect(header).toHaveTextContent('模型服务暂时不可用');
    expect(header).not.toHaveTextContent('次级工具异常');
    expect(screen.getByText('轨迹记录不完整')).toBeInTheDocument();
  });

  it('换模型行写明原模型、备用模型和原因', () => {
    const agentRun = run({
      steps: [step(1)],
      modelFallbacks: [{ stepId: 'step-1', failedModel: 'mimo-v2.6-flash', fallbackModel: 'deepseek-chat', reason: 'first_output_timeout' }],
    });
    renderProcess(agentRun, { isVisible: true });
    expect(screen.getByText('已换备用模型')).toBeInTheDocument();
    expect(screen.getByText('mimo-v2.6-flash → deepseek-chat · 首字超时')).toBeInTheDocument();
  });

  it('思考行收起时只预览第一句，正在输出的最后一段展开全文', () => {
    const agentRun = run({ status: 'running', steps: [step(1, { status: 'running', contentBlockIds: ['t1', 't2'] })] });
    const blocks = [thinking('t1', '先查天气。再看路线。'), thinking('t2', '正在比较两条路线')];
    render(
      <AgentProcess
        run={agentRun}
        rows={buildAgentProcessRows(blocks, agentRun)}
        thinkingStreaming
        isVisible
        onToggle={vi.fn()}
        trajectoryStatus="recording"
      />,
    );
    expect(screen.getByRole('button', { name: /思考先查天气。/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('再看路线。', { exact: false })).toBeNull();
    expect(screen.getByRole('button', { name: /思考中/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('正在比较两条路线')).toBeInTheDocument();
  });

  it('没有过程行时标题不可展开', () => {
    renderProcess(run());
    expect(screen.queryByRole('button', { name: /已完成/ })).toBeNull();
    expect(screen.getByRole('region', { name: 'Agent 运行过程' })).toHaveTextContent('已完成');
  });

  it.each(['pending', 'unconfirmed'] as const)('停止%s时不冒充运行中，也不继续计时', (status) => {
    vi.useFakeTimers();
    const stopped = run({ status: 'running', stopConfirmation: { status, requestedAt: 2000 } });
    renderProcess(stopped, { trajectoryStatus: 'recording' });
    const process = screen.getByRole('region', { name: 'Agent 运行过程' });
    expect(process).toHaveTextContent(status === 'pending' ? '正在确认停止' : '停止结果未确认');
    expect(process).not.toHaveTextContent('用时');
  });

  it('历史消息用同一运行的服务端耗时，不借用另一个 attempt 的', () => {
    const historical = run({ steps: [step(1, { startedAt: 0, completedAt: 800, toolCalls: [call({ startedAt: 0, completedAt: 800 })] })] });
    const view = renderProcess(historical, { runSummary: summary() });
    expect(screen.getByRole('button', { name: /已完成/ })).toHaveTextContent('用时 19.04 秒');

    view.rerender(
      <AgentProcess run={historical} rows={buildAgentProcessRows([], historical)} thinkingStreaming={false} isVisible={false} onToggle={vi.fn()} trajectoryStatus="complete" runSummary={summary({ run_id: 'run-2' })} />,
    );
    expect(screen.getByRole('button', { name: /已完成/ })).not.toHaveTextContent('用时');
  });

  it('运行中按服务端起点持续计时', () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse('2026-08-26T02:28:00.000Z'));
    renderProcess(
      run({ status: 'running', steps: [] }),
      { trajectoryStatus: 'recording', runSummary: summary({ status: 'running', duration_ms: null, ended_at: null }) },
    );
    const process = screen.getByRole('region', { name: 'Agent 运行过程' });
    expect(process).toHaveTextContent('处理中用时 2 秒');
    act(() => vi.advanceTimersByTime(1_000));
    expect(process).toHaveTextContent('处理中用时 3 秒');
    expect(process.querySelector('[aria-live]')).toBeNull();
  });

  it('输出触达长度上限时说明回答已截断', () => {
    renderProcess(run({ status: 'limit_reached', limitReachedReason: 'output_length' }));
    expect(screen.getByText('回答达到长度上限，已截断')).toBeInTheDocument();
  });
});
