import { describe, expect, it } from 'vitest';
import type { AgentRunState } from '@/types/agentRun';
import type { ContentBlock } from '@/types/conversation';
import { buildThinkingTrace } from './thinkingTrace';

function run(steps: AgentRunState['steps']): AgentRunState {
  return {
    runId: 'run-1',
    messageId: 'm1',
    status: 'running',
    config: { maxSteps: 8, maxToolCalls: 20, timeoutS: 300 },
    totalSteps: steps.length,
    totalToolCalls: 0,
    lastSequence: 1,
    steps,
  };
}

describe('buildThinkingTrace', () => {
  it('历史消息按块顺序穿插思考与工具，并去掉第一轮思考之前的工具', () => {
    const blocks: ContentBlock[] = [
      { type: 'search', id: 's0', query: '更早的搜索', sources: [] },
      { type: 'thinking', id: 't1', thinking: '先读页面。' },
      { type: 'url_read', id: 'u1', url: 'https://nb.zol.com.cn/a', title: '万元轻薄本盘点' },
      { type: 'data_source', id: 'd1', schema_version: 1, category: 'map', provider: 'tencent_map', label: '腾讯地图' },
      { type: 'thinking', id: 't2', thinking: '信息够了。' },
      { type: 'text', id: 'x1', text: '回答' },
    ];

    expect(buildThinkingTrace(blocks)).toEqual([
      { kind: 'thinking', id: 't1', text: '先读页面。' },
      { kind: 'tool', id: 'u1', toolName: 'url_read', summary: '万元轻薄本盘点' },
      { kind: 'tool', id: 'd1', toolName: 'tencent_map', summary: '腾讯地图' },
      { kind: 'thinking', id: 't2', text: '信息够了。' },
    ]);
  });

  it('生成中按运行步骤排列：流槽位里工具结果排在思考前面也不影响顺序', () => {
    const blocks: ContentBlock[] = [
      { type: 'image_results', id: 'img', schema_version: 1, query: '外观', images: [] },
      { type: 'thinking', id: 't1', thinking: '先搜。' },
      { type: 'thinking', id: 't2', thinking: '再找图。' },
    ];
    const trace = buildThinkingTrace(blocks, run([
      {
        stepId: 's1', stepNumber: 1, status: 'completed', startedAt: 1, contentBlockIds: ['t1'],
        toolCalls: [{ toolCallId: 'c1', toolName: 'web_search', arguments: { query: '笔记本' }, status: 'success', startedAt: 1 }],
      },
      {
        stepId: 's2', stepNumber: 2, status: 'running', startedAt: 2, contentBlockIds: ['t2'],
        toolCalls: [{ toolCallId: 'c2', toolName: 'image_search', arguments: { query: '外观' }, status: 'running', startedAt: 2 }],
      },
    ]));

    expect(trace.map(entry => entry.id)).toEqual(['t1', 'c1', 't2', 'c2']);
  });

  it('有思考没挂到步骤上时退回块顺序，不丢思考', () => {
    const blocks: ContentBlock[] = [
      { type: 'thinking', id: 't1', thinking: '第一轮。' },
      { type: 'thinking', id: 't2', thinking: '第二轮。' },
    ];
    const trace = buildThinkingTrace(blocks, run([
      { stepId: 's1', stepNumber: 1, status: 'completed', startedAt: 1, contentBlockIds: ['t1'], toolCalls: [] },
    ]));

    expect(trace.map(entry => entry.id)).toEqual(['t1', 't2']);
  });

  it('没有思考内容时返回空', () => {
    expect(buildThinkingTrace([{ type: 'text', id: 'x', text: '回答' }])).toEqual([]);
  });
});
