import { describe, it, expect } from 'vitest';
import { buildChatFromServerConversation } from '@/lib/chat/conversationHydration';
import streamSliceReducer, {
  startStream,
  applyPlanSnapshot,
  initRun,
  updatePlanStep,
  updateRunProgress,
  upsertEvidenceItem,
  upsertToolDigest,
  pushStep,
  pushToolCall,
  mergeToolCallDelta,
  finalizeToolCall,
  finalizeStep,
  discardContentBlock,
  markLimitReached,
  finalizeRun,
  appendTextDelta,
  appendThinkingDelta,
  advanceTypewriter,
  endStream,
  selectStreamContentBlocks,
  selectFullStreamContentBlocks,
  setLastEntryId,
  setStreamStatus,
  updateContextUsage,
  upsertStaticContentBlock,
  receiveContextRequired,
  receiveContextResult,
  setContextRequestPhase,
  resetStreamState,
  EMPTY_STREAM_SLOT,
} from './streamSlice';

const reducer = streamSliceReducer;

function initial() {
  return reducer(undefined, { type: '@@INIT' });
}

/** 取某个会话的流槽位。流状态不再是全局一份，每次读都要说清楚读的是哪个会话。 */
function slot(state: ReturnType<typeof initial>, conversationId: string) {
  return state.byConversation[conversationId] ?? EMPTY_STREAM_SLOT;
}

/** run 事件属于某条流：槽位只由 startStream 建立，没有槽位的事件会被丢弃。
 *  此前扁平状态下可以不起流直接派 run 事件，现在必须先有流。 */
function startedAt(conversationId: string, messageId = 'm1') {
  return reducer(initial(), startStream({ conversationId, messageId }));
}

const baseConfig = { maxSteps: 8, maxToolCalls: 20, timeoutS: 300 };

function planStatus(state: ReturnType<typeof initial>, id: string, conversationId = 'c1') {
  return slot(state, conversationId).currentRun?.plan?.items.find(item => item.id === id)?.status;
}

describe('streamSlice — content blocks selector', () => {
  it('content_block_discarded 按 block id 幂等移除过程性正文', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    state = reducer(state, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    state = reducer(state, appendTextDelta({ conversationId: 'c1',
      blockId: 'tool-preamble',
      delta: '我先调用工具。',
      runId: 'r1',
      stepId: 's1',
    }));
    state = reducer(state, advanceTypewriter({ conversationId: 'c1', chars: 100 }));

    state = reducer(state, discardContentBlock({ conversationId: 'c1',
      runId: 'r1',
      blockId: 'tool-preamble',
      sequence: 2,
    }));
    const afterFirstDiscard = state;
    state = reducer(state, discardContentBlock({ conversationId: 'c1',
      runId: 'r1',
      blockId: 'tool-preamble',
      sequence: 3,
    }));

    expect(selectFullStreamContentBlocks(slot(state, 'c1'))).toEqual([]);
    expect(slot(state, 'c1').textBlocks['tool-preamble']).toBeUndefined();
    expect(slot(state, 'c1').totalTextLength).toBe(0);
    expect(slot(state, 'c1').displayedTextLength).toBe(0);
    expect(slot(afterFirstDiscard, 'c1').currentRun?.steps[0].contentBlockIds).toEqual([]);
    expect(slot(state, 'c1').currentRun?.steps[0].contentBlockIds).toEqual([]);
  });

  it('content_block_discarded 同时移除静态富结果块且保留其他块', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    state = reducer(state, upsertStaticContentBlock({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      block: {
        type: 'place_results',
        id: 'places-old',
        schema_version: 1,
        provider: 'amap',
        query: '旧结果',
        status: 'success',
        result_count: 1,
        places: [{ provider_place_id: 'p-old', name: '旧地点' }],
        limitations: [],
      },
    }));
    state = reducer(state, upsertStaticContentBlock({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      block: {
        type: 'place_results',
        id: 'places-keep',
        schema_version: 1,
        provider: 'amap',
        query: '保留结果',
        status: 'success',
        result_count: 1,
        places: [{ provider_place_id: 'p-keep', name: '保留地点' }],
        limitations: [],
      },
    }));

    state = reducer(state, discardContentBlock({ conversationId: 'c1',
      runId: 'r1',
      blockId: 'places-old',
      sequence: 3,
    }));

    expect(slot(state, 'c1').staticBlocks).toEqual([
      expect.objectContaining({ id: 'places-keep' }),
    ]);
    expect(selectFullStreamContentBlocks(slot(state, 'c1'))).toEqual([
      expect.objectContaining({ id: 'places-keep' }),
    ]);
  });

  it('在模型正文到达前 upsert 结构化结果块，并按 id 替换而不重复', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    state = reducer(state, upsertStaticContentBlock({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      block: {
        type: 'place_results',
        id: 'places-1',
        schema_version: 1,
        provider: 'amap',
        query: '烤肉',
        status: 'success',
        result_count: 1,
        places: [{ provider_place_id: 'p1', name: '第一家烤肉' }],
        limitations: [],
      },
    }));

    expect(selectStreamContentBlocks(slot(state, 'c1'))).toEqual([
      expect.objectContaining({ type: 'place_results', id: 'places-1', result_count: 1 }),
    ]);

    state = reducer(state, upsertStaticContentBlock({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      block: {
        type: 'place_results',
        id: 'places-1',
        schema_version: 1,
        provider: 'amap',
        query: '烤肉',
        status: 'success',
        result_count: 2,
        places: [
          { provider_place_id: 'p1', name: '第一家烤肉' },
          { provider_place_id: 'p2', name: '第二家烤肉' },
        ],
        limitations: [],
      },
    }));
    state = reducer(state, appendTextDelta({ conversationId: 'c1', blockId: 'text-1', delta: '推荐如下。' }));
    state = reducer(state, advanceTypewriter({ conversationId: 'c1', chars: 5 }));

    const blocks = selectStreamContentBlocks(slot(state, 'c1'));
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ type: 'place_results', id: 'places-1', result_count: 2 });
    expect(blocks[1]).toEqual({ type: 'text', id: 'text-1', text: '推荐如下。' });
  });

  it('相同 stream 状态重复选择时复用结果引用', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, appendTextDelta({ conversationId: 'c1', blockId: 'text-1', delta: '流式' }));
    state = reducer(state, advanceTypewriter({ conversationId: 'c1', chars: 2 }));

    const first = selectStreamContentBlocks(slot(state, 'c1'));
    const second = selectStreamContentBlocks(slot(state, 'c1'));

    expect(second).toBe(first);
    expect(second).toEqual([{ type: 'text', id: 'text-1', text: '流式' }]);

    state = reducer(state, appendTextDelta({ conversationId: 'c1', blockId: 'text-1', delta: '回答' }));
    state = reducer(state, advanceTypewriter({ conversationId: 'c1', chars: 2 }));
    const updated = selectStreamContentBlocks(slot(state, 'c1'));

    expect(updated).not.toBe(first);
    expect(updated).toEqual([{ type: 'text', id: 'text-1', text: '流式回答' }]);
  });

  it('A→B→A 交错选择时复用各自引用且不串值', () => {
    const stateA = reducer(initial(), startStream({
      conversationId: 'c-a',
      messageId: 'm-a',
      staticBlocks: [{ type: 'text', id: 'static-a', text: '回答 A' }],
    }));
    const stateB = reducer(initial(), startStream({
      conversationId: 'c-b',
      messageId: 'm-b',
      staticBlocks: [{ type: 'text', id: 'static-b', text: '回答 B' }],
    }));

    const firstA = selectStreamContentBlocks(slot(stateA, 'c-a'));
    const selectedB = selectStreamContentBlocks(slot(stateB, 'c-b'));
    const secondA = selectStreamContentBlocks(slot(stateA, 'c-a'));

    expect(secondA).toBe(firstA);
    expect(secondA).toEqual([{ type: 'text', id: 'static-a', text: '回答 A' }]);
    expect(selectedB).not.toBe(firstA);
    expect(selectedB).toEqual([{ type: 'text', id: 'static-b', text: '回答 B' }]);
  });

  it('static、thinking 或 typewriter 输入变化时生成新引用并返回正确内容', () => {
    const baseState = reducer(initial(), startStream({
      conversationId: 'c1',
      messageId: 'm1',
      staticBlocks: [{ type: 'text', id: 'static-1', text: '历史回答' }],
    }));
    const baseBlocks = selectStreamContentBlocks(slot(baseState, 'c1'));

    const staticChangedState = {
      ...baseState,
      byConversation: {
        ...baseState.byConversation,
        c1: {
          ...slot(baseState, 'c1'),
          staticBlocks: [{ type: 'text' as const, id: 'static-2', text: '恢复后的回答' }],
        },
      },
    };
    const staticChangedBlocks = selectStreamContentBlocks(slot(staticChangedState, 'c1'));
    expect(staticChangedBlocks).not.toBe(baseBlocks);
    expect(staticChangedBlocks).toEqual([
      { type: 'text', id: 'static-2', text: '恢复后的回答' },
    ]);

    let thinkingState = reducer(staticChangedState, appendThinkingDelta({ conversationId: 'c1',
      blockId: 'thinking-1',
      delta: '第一步',
    }));
    const firstThinkingBlocks = selectStreamContentBlocks(slot(thinkingState, 'c1'));
    thinkingState = reducer(thinkingState, appendThinkingDelta({ conversationId: 'c1',
      blockId: 'thinking-1',
      delta: '继续思考',
    }));
    const updatedThinkingBlocks = selectStreamContentBlocks(slot(thinkingState, 'c1'));
    expect(updatedThinkingBlocks).not.toBe(firstThinkingBlocks);
    expect(updatedThinkingBlocks).toEqual([
      { type: 'text', id: 'static-2', text: '恢复后的回答' },
      { type: 'thinking', id: 'thinking-1', thinking: '第一步继续思考' },
    ]);

    const textPendingState = reducer(thinkingState, appendTextDelta({ conversationId: 'c1',
      blockId: 'text-1',
      delta: '新回答',
    }));
    const beforeTypewriterBlocks = selectStreamContentBlocks(slot(textPendingState, 'c1'));
    const typewriterState = reducer(textPendingState, advanceTypewriter({ conversationId: 'c1', chars: 3 }));
    const afterTypewriterBlocks = selectStreamContentBlocks(slot(typewriterState, 'c1'));
    expect(afterTypewriterBlocks).not.toBe(beforeTypewriterBlocks);
    expect(afterTypewriterBlocks).toEqual([
      { type: 'text', id: 'static-2', text: '恢复后的回答' },
      { type: 'thinking', id: 'thinking-1', thinking: '第一步继续思考' },
      { type: 'text', id: 'text-1', text: '新回答' },
    ]);
  });
});

describe('streamSlice — agent run timeline', () => {
  it('定位请求只保存瞬态元数据，按 sequence 幂等并由匹配结果清理', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    state = reducer(state, receiveContextRequired({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-1',
      contextType: 'geolocation',
      purpose: 'nearby_search',
      reason: '搜索附近地点',
      expiresAt: 1_721_200_120,
      sequence: 1,
    }));

    expect(slot(state, 'c1').pendingContextRequest).toEqual(expect.objectContaining({
      conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-1',
      phase: 'required',
    }));
    expect(slot(state, 'c1').pendingContextRequest).not.toHaveProperty('location');

    state = reducer(state, setContextRequestPhase({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-1',
      phase: 'locating',
    }));
    expect(slot(state, 'c1').pendingContextRequest?.phase).toBe('locating');

    state = reducer(state, receiveContextRequired({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-old',
      contextType: 'geolocation',
      purpose: 'local_weather',
      reason: '旧事件',
      expiresAt: 1_721_200_120,
      sequence: 1,
    }));
    expect(slot(state, 'c1').pendingContextRequest?.requestId).toBe('ctx-1');

    state = reducer(state, receiveContextResult({ conversationId: 'c1',
      runId: 'another-run',
      requestId: 'ctx-1',
      contextType: 'geolocation',
      status: 'provided',
      sequence: 2,
    }));
    expect(slot(state, 'c1').pendingContextRequest).not.toBeNull();

    state = reducer(state, receiveContextResult({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-1',
      contextType: 'geolocation',
      status: 'provided',
      sequence: 2,
    }));
    expect(slot(state, 'c1').pendingContextRequest).toBeNull();
  });

  it('run 终态和 endStream 都清理未完成的定位请求', () => {
    let state = reducer(initial(), startStream({ conversationId: 'c1', messageId: 'm1' }));
    state = reducer(state, initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    state = reducer(state, receiveContextRequired({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-1',
      contextType: 'geolocation',
      purpose: 'nearby_search',
      reason: '搜索附近地点',
      expiresAt: 1_721_200_120,
      sequence: 1,
    }));
    state = reducer(state, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'completed', sequence: 2 }));
    expect(slot(state, 'c1').pendingContextRequest).toBeNull();

    state = reducer(state, receiveContextRequired({ conversationId: 'c1',
      runId: 'r1',
      requestId: 'ctx-2',
      contextType: 'geolocation',
      purpose: 'local_weather',
      reason: '查询本地天气',
      expiresAt: 1_721_200_120,
      sequence: 3,
    }));
    state = reducer(state, endStream({ conversationId: 'c1' }));
    expect(slot(state, 'c1').pendingContextRequest).toBeNull();
  });

  it('estimated 只进入 in-flight，final actual 才原子替换 confirmed', () => {
    let state = reducer(initial(), startStream({ conversationId: 'chat-a', messageId: 'msg-a' }));
    state = reducer(state, updateContextUsage({
      conversationId: 'chat-a',
      usage: {
        status: 'no_op',
        window_tokens: 1000,
        estimated_tokens_before: 400,
        estimated_tokens_after: 400,
        actual_prompt_tokens: null,
        removed_turns: 0,
        removed_messages: 0,
        removed_tool_transactions: 0,
        round_index: 1,
      },
      runId: 'run-a',
      messageId: 'server-msg-a',
      sequence: 1,
      phase: 'estimated',
    }));
    expect(slot(state, 'chat-a').contextUsage).toBeNull();
    expect(slot(state, 'chat-a').contextUsageInFlight).toMatchObject({
      estimated_tokens_after: 400,
      actual_prompt_tokens: null,
    });
    expect(slot(state, 'chat-a').contextUsageInFlightMeta).toMatchObject({ phase: 'estimated', roundIndex: 1 });

    state = reducer(state, updateContextUsage({
      conversationId: 'chat-a',
      usage: {
        status: 'no_op',
        window_tokens: 1000,
        estimated_tokens_before: 400,
        estimated_tokens_after: 400,
        actual_prompt_tokens: 410,
        removed_turns: 0,
        removed_messages: 0,
        removed_tool_transactions: 0,
        round_index: 1,
      },
      runId: 'run-a',
      messageId: 'server-msg-a',
      sequence: 2,
      phase: 'final',
    }));

    expect(slot(state, 'chat-a').contextUsage?.actual_prompt_tokens).toBe(410);
    expect(slot(state, 'chat-a').contextUsageMeta).toMatchObject({ phase: 'final', roundIndex: 1 });
    expect(slot(state, 'chat-a').contextUsageInFlightMeta).toMatchObject({ phase: 'final', roundIndex: 1 });
    state = reducer(state, endStream({ conversationId: 'chat-a' }));
    expect(slot(state, 'chat-a').contextUsage?.actual_prompt_tokens).toBe(410);
    expect(slot(state, 'chat-a').contextUsageInFlight?.actual_prompt_tokens).toBe(410);
    expect(slot(state, 'chat-a').conversationId).toBeNull();
    expect(slot(state, 'chat-a').contextUsageConversationId).toBe('chat-a');

    // 另一个会话开流不再清掉这一条的留尾：槽位各自独立，
    // chat-b 从空槽开始，chat-a 结束后留下的用量原样保留。
    state = reducer(state, startStream({ conversationId: 'chat-b', messageId: 'msg-b' }));
    expect(slot(state, 'chat-b').contextUsage).toBeNull();
    expect(slot(state, 'chat-b').contextUsageInFlight).toBeNull();
    expect(slot(state, 'chat-a').contextUsage?.actual_prompt_tokens).toBe(410);
  });

  it('别的会话的上下文事件写不进本会话', () => {
    // 槽位按 payload 里的会话 ID 路由，写不进本会话；chat-b 没有槽位时直接丢弃。
    let state = reducer(initial(), startStream({ conversationId: 'chat-a', messageId: 'msg-a' }));
    state = reducer(state, updateContextUsage({
      conversationId: 'chat-b',
      usage: {
        status: 'no_op',
        window_tokens: 1000,
        estimated_tokens_before: 400,
        estimated_tokens_after: 400,
        actual_prompt_tokens: null,
        removed_turns: 0,
        removed_messages: 0,
        removed_tool_transactions: 0,
        round_index: 1,
      },
      runId: 'run-b',
      messageId: 'server-msg-b',
      sequence: 1,
      phase: 'estimated',
    }));
    expect(slot(state, 'chat-a').contextUsage).toBeNull();
    expect(slot(state, 'chat-a').contextUsageInFlight).toBeNull();
  });

  it('同一 run 按 sequence replace 且 final 优先，重连重放不得倒退', () => {
    let state = reducer(initial(), startStream({ conversationId: 'chat-a', messageId: 'msg-a' }));
    const usage = {
      status: 'no_op',
      window_tokens: 1000,
      estimated_tokens_before: 400,
      estimated_tokens_after: 400,
      actual_prompt_tokens: null,
      removed_turns: 0,
      removed_messages: 0,
      removed_tool_transactions: 0,
      round_index: 1,
    };
    state = reducer(state, updateContextUsage({
      conversationId: 'chat-a', usage: { ...usage, actual_prompt_tokens: 410 },
      runId: 'run-a', messageId: 'server-msg-a', sequence: 5, phase: 'final',
    }));
    state = reducer(state, updateContextUsage({
      conversationId: 'chat-a', usage: { ...usage, actual_prompt_tokens: 999 },
      runId: 'run-a', messageId: 'server-msg-a', sequence: 4, phase: 'final',
    }));
    state = reducer(state, updateContextUsage({
      conversationId: 'chat-a', usage: { ...usage, actual_prompt_tokens: null },
      runId: 'run-a', messageId: 'server-msg-a', sequence: 6, phase: 'estimated',
    }));

    expect(slot(state, 'chat-a').contextUsage?.actual_prompt_tokens).toBe(410);
    expect(slot(state, 'chat-a').contextUsageMeta).toMatchObject({ sequence: 5, phase: 'final', roundIndex: 1 });
    expect(slot(state, 'chat-a').contextUsageInFlightMeta).toMatchObject({ sequence: 5, phase: 'final', roundIndex: 1 });

    state = reducer(state, updateContextUsage({ conversationId: 'chat-a',
      usage: { ...usage, round_index: 2, estimated_tokens_after: 430 },
      runId: 'run-a', messageId: 'server-msg-a', sequence: 7, phase: 'estimated',
    }));
    expect(slot(state, 'chat-a').contextUsage).toMatchObject({
      round_index: 1,
      actual_prompt_tokens: 410,
    });
    expect(slot(state, 'chat-a').contextUsageInFlight).toMatchObject({
      round_index: 2,
      estimated_tokens_after: 430,
      actual_prompt_tokens: null,
    });
    expect(slot(state, 'chat-a').contextUsageMeta).toMatchObject({ sequence: 5, phase: 'final', roundIndex: 1 });
    expect(slot(state, 'chat-a').contextUsageInFlightMeta).toMatchObject({ sequence: 7, phase: 'estimated', roundIndex: 2 });

    state = reducer(state, updateContextUsage({ conversationId: 'chat-a',
      usage: { ...usage, round_index: 2, estimated_tokens_after: 430 },
      runId: 'run-a', messageId: 'server-msg-a', sequence: 8, phase: 'final',
    }));
    expect(slot(state, 'chat-a').contextUsage).toMatchObject({
      round_index: 1,
      actual_prompt_tokens: 410,
    });
    expect(slot(state, 'chat-a').contextUsageInFlightMeta).toMatchObject({ sequence: 8, phase: 'final', roundIndex: 2 });
  });
  it('initRun 创建 currentRun (status=running, lastSequence=0)', () => {
    const state = reducer(startedAt('c1'), initRun({ conversationId: 'c1',
      runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0,
    }));
    expect(slot(state, 'c1').currentRun?.runId).toBe('r1');
    expect(slot(state, 'c1').currentRun?.status).toBe('running');
    expect(slot(state, 'c1').currentRun?.lastSequence).toBe(0);
    expect(slot(state, 'c1').currentRun?.steps).toHaveLength(0);
    expect(slot(state, 'c1').currentRun?.evidence).toEqual([]);
    expect(slot(state, 'c1').currentRun?.toolDigests).toEqual([]);
  });

  it('updateRunProgress 写入 progress 并按 sequence 幂等', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      progress: { phase: 'researching', label: '正在搜索相关资料', completedSteps: 1, totalSteps: 4 },
    }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      progress: { phase: 'answering', label: '旧事件不应覆盖' },
    }));

    expect(slot(s, 'c1').currentRun?.progress?.label).toBe('正在搜索相关资料');
    expect(slot(s, 'c1').currentRun?.lastSequence).toBe(1);
  });

  it('plan reducers 按 revision 更新并忽略旧 revision', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      plan: { planId: 'plan-r1', revision: 2, items: [] },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      planId: 'plan-r1',
      revision: 2,
      item: { id: 'search', title: '搜索资料', status: 'running', kind: 'search', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 3,
      planId: 'plan-r1',
      revision: 3,
      item: { id: 'search', title: '搜索资料', status: 'completed', kind: 'search', toolNames: ['web_search'], evidenceItemIds: ['ev-1'] },
    }));

    expect(slot(s, 'c1').currentRun?.plan?.revision).toBe(3);
    expect(slot(s, 'c1').currentRun?.plan?.items).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.plan?.items[0].status).toBe('completed');
  });

  it('plan snapshot 同时按 sequence 和 revision 防乱序，并允许新 plan 重置 revision', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 5,
      plan: {
        planId: 'plan-r1',
        revision: 3,
        mode: 'on',
        source: 'model',
        reason: 'model_update',
        items: [{
          id: 'research',
          title: '研究最新资料',
          status: 'running',
          kind: 'other',
          toolNames: [],
          evidenceItemIds: [],
          dependsOn: [],
          plannedTools: ['web_search'],
        }],
      },
    }));

    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 9,
      plan: {
        planId: 'plan-r1',
        revision: 2,
        mode: 'on',
        source: 'model',
        reason: 'model_update',
        items: [{
          id: 'research',
          title: '旧计划不得覆盖',
          status: 'pending',
          kind: 'other',
          toolNames: [],
          evidenceItemIds: [],
        }],
      },
    }));

    expect(slot(s, 'c1').currentRun?.plan?.revision).toBe(3);
    expect(slot(s, 'c1').currentRun?.plan?.items[0].title).toBe('研究最新资料');
    expect(slot(s, 'c1').currentRun?.lastSequence).toBe(5);

    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 9,
      progress: { phase: 'researching', label: '同 sequence 的有效事件' },
    }));
    expect(slot(s, 'c1').currentRun?.progress?.label).toBe('同 sequence 的有效事件');
    expect(slot(s, 'c1').currentRun?.lastSequence).toBe(9);

    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 8,
      plan: {
        planId: 'plan-r1',
        revision: 4,
        items: [],
      },
    }));
    expect(slot(s, 'c1').currentRun?.plan?.revision).toBe(3);

    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 10,
      plan: {
        planId: 'plan-r2',
        revision: 1,
        mode: 'auto',
        source: 'observed',
        reason: 'legacy_observed',
        items: [],
      },
    }));
    expect(slot(s, 'c1').currentRun?.plan).toMatchObject({
      planId: 'plan-r2',
      revision: 1,
      mode: 'auto',
      source: 'observed',
      reason: 'legacy_observed',
    });
    expect(slot(s, 'c1').currentRun?.lastSequence).toBe(10);
  });

  it('plan step update 同步根元数据与 item 依赖工具信息', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'm1',
      config: baseConfig,
      sequence: 0,
    }));
    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      plan: {
        planId: 'plan-r1',
        revision: 1,
        items: [],
      },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      planId: 'plan-r1',
      revision: 2,
      mode: 'on',
      source: 'model',
      reason: 'plan_required',
      item: {
        id: 'compare',
        title: '比较候选方案',
        status: 'running',
        kind: 'other',
        toolNames: [],
        evidenceItemIds: [],
        dependsOn: ['research'],
        plannedTools: ['route_compare'],
      },
    }));

    expect(slot(s, 'c1').currentRun?.plan).toMatchObject({
      revision: 2,
      mode: 'on',
      source: 'model',
      reason: 'plan_required',
      items: [{
        id: 'compare',
        dependsOn: ['research'],
        plannedTools: ['route_compare'],
      }],
    });
  });

  it('agent plan v2 实时推进搜索、读取、整理回答状态', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, applyPlanSnapshot({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      plan: {
        planId: 'plan-r1',
        revision: 1,
        items: [
          { id: 'understand', title: '理解问题', status: 'running', kind: 'reasoning', toolNames: [], evidenceItemIds: [] },
          { id: 'search', title: '查找资料', status: 'pending', kind: 'search', toolNames: ['web_search'], evidenceItemIds: [] },
          { id: 'read', title: '读取关键来源', status: 'pending', kind: 'read', toolNames: ['web_search'], evidenceItemIds: [] },
          { id: 'answer', title: '整理回答', status: 'pending', kind: 'answer', toolNames: [], evidenceItemIds: [] },
        ],
      },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      planId: 'plan-r1',
      revision: 3,
      item: { id: 'understand', title: '理解问题', status: 'completed', kind: 'reasoning', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 3,
      planId: 'plan-r1',
      revision: 4,
      item: { id: 'search', title: '查找资料', status: 'running', kind: 'search', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 4,
      progress: {
        phase: 'researching',
        label: '正在查找资料',
        completedSteps: 1,
        completedToolCalls: 0,
        maxToolCalls: 20,
      },
    }));

    expect(planStatus(s, 'understand')).toBe('completed');
    expect(planStatus(s, 'search')).toBe('running');
    expect(planStatus(s, 'read')).toBe('pending');
    expect(slot(s, 'c1').currentRun?.progress).toMatchObject({ phase: 'researching', completedToolCalls: 0, maxToolCalls: 20 });

    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 5,
      planId: 'plan-r1',
      revision: 5,
      item: { id: 'search', title: '查找资料', status: 'completed', kind: 'search', toolNames: ['web_search'], evidenceItemIds: [] },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 6,
      planId: 'plan-r1',
      revision: 6,
      item: { id: 'read', title: '读取关键来源', status: 'running', kind: 'read', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 7,
      progress: {
        phase: 'reading',
        label: '正在读取关键来源',
        completedSteps: 2,
        completedToolCalls: 1,
        maxToolCalls: 20,
      },
    }));

    expect(planStatus(s, 'search')).toBe('completed');
    expect(planStatus(s, 'read')).toBe('running');
    expect(planStatus(s, 'answer')).toBe('pending');
    expect(slot(s, 'c1').currentRun?.progress).toMatchObject({ phase: 'reading', completedToolCalls: 1, maxToolCalls: 20 });

    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 8,
      planId: 'plan-r1',
      revision: 7,
      item: { id: 'read', title: '读取关键来源', status: 'completed', kind: 'read', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 9,
      planId: 'plan-r1',
      revision: 8,
      item: { id: 'answer', title: '整理回答', status: 'running', kind: 'answer', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 10,
      progress: {
        phase: 'synthesizing',
        label: '正在整理回答',
        completedSteps: 3,
        completedToolCalls: 1,
        maxToolCalls: 20,
      },
    }));

    expect(planStatus(s, 'read')).toBe('completed');
    expect(planStatus(s, 'answer')).toBe('running');

    s = reducer(s, updatePlanStep({ conversationId: 'c1',
      runId: 'r1',
      sequence: 11,
      planId: 'plan-r1',
      revision: 9,
      item: { id: 'answer', title: '整理回答', status: 'completed', kind: 'answer', toolNames: [], evidenceItemIds: [] },
    }));
    s = reducer(s, updateRunProgress({ conversationId: 'c1',
      runId: 'r1',
      sequence: 12,
      progress: {
        phase: 'answering',
        label: '已完成回答整理',
        completedSteps: 4,
        completedToolCalls: 1,
        maxToolCalls: 20,
      },
    }));

    expect(planStatus(s, 'answer')).toBe('completed');
    expect(slot(s, 'c1').currentRun?.progress).toMatchObject({
      phase: 'answering',
      completedSteps: 4,
      completedToolCalls: 1,
      maxToolCalls: 20,
    });
  });

  it('upsertEvidenceItem 和 upsertToolDigest 不重复，并在后续事件省略编号时保留 citationIndex', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'candidate',
        title: '来源',
        claim: '发现',
        citationIndex: 3,
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: { id: 'ev-1', kind: 'web', status: 'used', title: '来源', claim: '已采用', usedByFinalAnswer: true },
    }));
    s = reducer(s, upsertToolDigest({ conversationId: 'c1',
      runId: 'r1',
      sequence: 3,
      digest: {
        toolCallId: 'tc1',
        toolName: 'web_search',
        status: 'success',
        title: '找到结果',
        summary: '摘要',
        keyFindings: [],
        sourceRefs: ['ev-1'],
        truncated: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.evidence?.[0].status).toBe('used');
    expect(slot(s, 'c1').currentRun?.evidence?.[0].citationIndex).toBe(3);
    expect(slot(s, 'c1').currentRun?.toolDigests).toHaveLength(1);
  });

  it('selected 后到达 candidate 不会把状态和主要字段降级', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'selected',
        title: '已选来源',
        url: 'https://example.com/selected',
        domain: 'example.com',
        citationIndex: 2,
        claim: '已选结论',
        snippet: '已选摘要',
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'candidate',
        title: '迟到候选',
        url: 'https://example.com/candidate',
        domain: 'candidate.example.com',
        citationIndex: 9,
        claim: '候选结论',
        snippet: '候选摘要',
        usedByFinalAnswer: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence?.[0]).toMatchObject({
      status: 'selected',
      title: '已选来源',
      url: 'https://example.com/selected',
      citationIndex: 2,
      claim: '已选结论',
      snippet: '已选摘要',
    });
  });

  it('read_success 后到达 read_failed 不会把读取状态降级', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'read_success',
        title: '成功读取',
        url: 'https://example.com/report',
        claim: '已读取全文',
        snippet: '正文摘要',
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'read_failed',
        title: '读取失败',
        claim: '稍后到达的失败事件',
        usedByFinalAnswer: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence?.[0]).toMatchObject({
      status: 'read_success',
      title: '成功读取',
      url: 'https://example.com/report',
      claim: '已读取全文',
      snippet: '正文摘要',
    });
  });

  it('高优先级事件的空字段由旧事件补齐', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'candidate',
        title: '旧标题',
        url: 'https://example.com/report',
        domain: 'example.com',
        citationIndex: 7,
        claim: '旧结论',
        snippet: '旧摘要',
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'selected',
        title: '',
        url: '',
        claim: '',
        snippet: '',
        usedByFinalAnswer: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence?.[0]).toMatchObject({
      status: 'selected',
      title: '旧标题',
      url: 'https://example.com/report',
      domain: 'example.com',
      citationIndex: 7,
      claim: '旧结论',
      snippet: '旧摘要',
    });
  });

  it('反向到达的高优先级事件升级状态，并只用旧事件补自身缺失字段', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'candidate',
        title: '候选标题',
        url: 'https://example.com/report',
        domain: 'example.com',
        citationIndex: 4,
        claim: '候选结论',
        snippet: '候选摘要',
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: {
        id: 'ev-1',
        kind: 'web',
        status: 'used',
        title: '最终标题',
        claim: '最终结论',
        snippet: '',
        usedByFinalAnswer: true,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence?.[0]).toMatchObject({
      status: 'used',
      title: '最终标题',
      url: 'https://example.com/report',
      domain: 'example.com',
      citationIndex: 4,
      claim: '最终结论',
      snippet: '候选摘要',
      usedByFinalAnswer: true,
    });
  });

  it('流式 evidence 应用合并后 cap，并与后端历史 snapshot 的 12 条结果等价', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    for (let index = 1; index <= 14; index += 1) {
      s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
        runId: 'r1',
        sequence: index,
        evidence: {
          id: `ev-${index}`,
          kind: 'web',
          status: index === 1 ? 'used' : index === 2 ? 'read_success' : 'candidate',
          title: `来源 ${index}`,
          url: `https://example.com/${index}`,
          domain: 'example.com',
          citationIndex: index,
          claim: `发现 ${index}`,
          snippet: `摘要 ${index}`,
          usedByFinalAnswer: index === 1,
        },
      }));
    }

    const expectedIds = [
      'ev-1',
      'ev-2',
      'ev-5',
      'ev-6',
      'ev-7',
      'ev-8',
      'ev-9',
      'ev-10',
      'ev-11',
      'ev-12',
      'ev-13',
      'ev-14',
    ];
    expect(slot(s, 'c1').currentRun?.evidence).toHaveLength(12);
    expect(slot(s, 'c1').currentRun?.evidence?.map(item => item.id)).toEqual(expectedIds);

    const historical = buildChatFromServerConversation({
      id: 'chat-1',
      title: '历史快照',
      model_id: 'gpt',
      messages: [{
        id: 'm1',
        role: 'assistant',
        content: [],
        agent_run: {
          run_id: 'r1',
          status: 'completed',
          config: {},
          progress: {
            evidence: expectedIds.map((id) => {
              const index = Number(id.slice(3));
              return {
                id,
                kind: 'web',
                status: index === 1 ? 'used' : index === 2 ? 'read_success' : 'candidate',
                title: `来源 ${index}`,
                url: `https://example.com/${index}`,
                domain: 'example.com',
                citation_index: index,
                claim: `发现 ${index}`,
                snippet: `摘要 ${index}`,
                used_by_final_answer: index === 1,
              };
            }),
          },
        },
      }],
    } as any);

    expect(slot(s, 'c1').currentRun?.evidence).toEqual(historical.messages[0].agent_run?.evidence);
  });

  it('修参成功即使后续 digest 事件丢失也立即清理匹配摘要并恢复对应 tool call', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    for (const [startSequence, completeSequence, digestSequence, toolCallId, repairId] of [
      [2, 3, 4, 'tc-a', 'repair_aaaaaaaaaaaaaaaa'],
      [5, 6, 7, 'tc-b', 'repair_bbbbbbbbbbbbbbbb'],
    ] as const) {
      s = reducer(s, pushToolCall({ conversationId: 'c1',
        runId: 'r1',
        stepId: 's1',
        toolCallId,
        toolName: 'weather_forecast',
        arguments: { location: '南山区' },
        sequence: startSequence,
      }));
      s = reducer(s, finalizeToolCall({ conversationId: 'c1',
        runId: 'r1',
        toolCallId,
        status: 'degraded',
        durationMs: 0,
        resultSummary: {
          kind: 'weather',
          truncated: false,
          repair_state: 'retrying',
          repair_id: repairId,
        },
        sequence: completeSequence,
      }));
      s = reducer(s, upsertToolDigest({ conversationId: 'c1',
        runId: 'r1',
        sequence: digestSequence,
        digest: {
          toolCallId,
          toolName: 'weather_forecast',
          status: 'degraded',
          title: '正在修正工具参数',
          summary: '参数修正中',
          keyFindings: [],
          sourceRefs: [],
          truncated: false,
          repairState: 'retrying',
          repairId,
        },
      }));
    }
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1',
      stepId: 's1',
      toolCallId: 'tc-success',
      toolName: 'weather_forecast',
      arguments: { location: '深圳市南山区', location_source: 'named' },
      sequence: 8,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1',
      toolCallId: 'tc-success',
      status: 'success',
      durationMs: 12,
      resultSummary: {
        kind: 'weather',
        truncated: false,
        repair_state: 'resolved',
        resolves_repair_id: 'repair_aaaaaaaaaaaaaaaa',
      },
      sequence: 9,
    }));

    expect(slot(s, 'c1').currentRun?.toolDigests?.map(digest => digest.toolCallId)).toEqual(['tc-b']);
    let calls = slot(s, 'c1').currentRun?.steps.flatMap(step => step.toolCalls) ?? [];
    expect(calls.find(call => call.toolCallId === 'tc-a')?.status).toBe('success');
    expect(calls.find(call => call.toolCallId === 'tc-a')?.resultSummary?.repair_state).toBe('resolved');
    expect(calls.find(call => call.toolCallId === 'tc-b')?.status).toBe('degraded');

    s = reducer(s, upsertToolDigest({ conversationId: 'c1',
      runId: 'r1',
      sequence: 10,
      digest: {
        toolCallId: 'tc-success',
        toolName: 'weather_forecast',
        status: 'success',
        title: '天气查询完成',
        summary: '工具返回了可用结果。',
        keyFindings: [],
        sourceRefs: [],
        truncated: false,
        repairState: 'resolved',
        repairId: 'repair_aaaaaaaaaaaaaaaa',
      },
    }));

    expect(slot(s, 'c1').currentRun?.toolDigests?.map(digest => digest.toolCallId)).toEqual(['tc-b', 'tc-success']);
    calls = slot(s, 'c1').currentRun?.steps.flatMap(step => step.toolCalls) ?? [];
    expect(calls.find(call => call.toolCallId === 'tc-a')?.status).toBe('success');
    expect(calls.find(call => call.toolCallId === 'tc-a')?.resultSummary?.repair_state).toBe('resolved');
    expect(calls.find(call => call.toolCallId === 'tc-b')?.status).toBe('degraded');
  });

  it('upsertEvidenceItem 接收 selected/read_success evidence 状态', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      evidence: {
        id: 'ev-web-1',
        kind: 'web',
        status: 'selected',
        title: '建议深读来源',
        url: 'https://example.com/report',
        claim: '建议深读：官方来源',
        usedByFinalAnswer: false,
      },
    }));
    s = reducer(s, upsertEvidenceItem({ conversationId: 'c1',
      runId: 'r1',
      sequence: 2,
      evidence: {
        id: 'ev-web-1',
        kind: 'web',
        status: 'read_success',
        title: '已读取来源',
        url: 'https://example.com/report',
        claim: '已读取网页内容。',
        usedByFinalAnswer: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.evidence).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.evidence?.[0].status).toBe('read_success');
  });

  it('pushStep 添加 running step + 更新 totalSteps', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    expect(slot(s, 'c1').currentRun?.steps).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('running');
    expect(slot(s, 'c1').currentRun?.totalSteps).toBe(1);
  });

  it('pushToolCall 挂到对应 step + 累加 totalToolCalls', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('running');
    expect(slot(s, 'c1').currentRun?.totalToolCalls).toBe(1);
  });

  it('工具调用和摘要保留 planItemId，completed 可为重连中的 started 事件补回关联', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1',
      stepId: 's1',
      toolCallId: 't1',
      toolName: 'route_compare',
      arguments: {},
      sequence: 2,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1',
      toolCallId: 't1',
      planItemId: 'compare-routes',
      status: 'success',
      durationMs: 42,
      sequence: 3,
    }));
    s = reducer(s, upsertToolDigest({ conversationId: 'c1',
      runId: 'r1',
      sequence: 4,
      digest: {
        toolCallId: 't1',
        planItemId: 'compare-routes',
        toolName: 'route_compare',
        status: 'success',
        title: '路线比较完成',
        summary: '已比较 3 种出行方式',
        keyFindings: [],
        sourceRefs: [],
        truncated: false,
      },
    }));
    s = reducer(s, upsertToolDigest({ conversationId: 'c1',
      runId: 'r1',
      sequence: 5,
      digest: {
        toolCallId: 't1',
        toolName: 'route_compare',
        status: 'success',
        title: '路线比较已更新',
        summary: '补充了路线细节',
        keyFindings: [],
        sourceRefs: [],
        truncated: false,
      },
    }));

    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0]).toMatchObject({
      toolCallId: 't1',
      planItemId: 'compare-routes',
      status: 'success',
    });
    expect(slot(s, 'c1').currentRun?.toolDigests?.[0]).toMatchObject({
      toolCallId: 't1',
      planItemId: 'compare-routes',
    });
  });

  it('旧工具事件缺少 planItemId 时保持兼容', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1',
      stepId: 's1',
      toolCallId: 't1',
      toolName: 'web_search',
      arguments: {},
      sequence: 2,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1',
      toolCallId: 't1',
      status: 'success',
      durationMs: 42,
      sequence: 3,
    }));

    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0]).not.toHaveProperty('planItemId');
  });

  it('mergeToolCallDelta 浅合并字段不覆盖 status', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: {}, sequence: 2,
    }));
    s = reducer(s, mergeToolCallDelta({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1',
      delta: { resultSummary: { kind: 'search', truncated: false } } as Record<string, unknown>,
      sequence: 3,
    }));
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('running');
    expect((slot(s, 'c1').currentRun?.steps[0].toolCalls[0] as unknown as Record<string, unknown>).resultSummary).toEqual({ kind: 'search', truncated: false });
  });

  it('finalizeToolCall 把 running → success + 写 resultSummary', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: {}, sequence: 2,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1',
      status: 'success', durationMs: 42,
      resultSummary: { kind: 'search', count: 5, truncated: false },
      sequence: 3,
    }));
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('success');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].resultSummary?.count).toBe(5);
  });

  it('finalizeStep 把 step → completed + completedAt', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, finalizeStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', sequence: 2 }));
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('completed');
    expect(slot(s, 'c1').currentRun?.steps[0].completedAt).toBeDefined();
  });

  it('finalizeStep 移除工具调用回合的过程性正文，保留后续最终回答', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1',
      blockId: 'tool-preamble',
      delta: '好的，我先调用路线工具。',
      runId: 'r1',
      stepId: 's1',
    }));
    s = reducer(s, advanceTypewriter({ conversationId: 'c1', chars: 100 }));
    s = reducer(s, finalizeStep({ conversationId: 'c1',
      runId: 'r1',
      stepId: 's1',
      toolCallCount: 1,
      sequence: 2,
    }));

    expect(slot(s, 'c1').textBlocks['tool-preamble']).toBeUndefined();
    expect(slot(s, 'c1').blockOrder).not.toContain('tool-preamble');
    expect(slot(s, 'c1').totalTextLength).toBe(0);
    expect(slot(s, 'c1').displayedTextLength).toBe(0);

    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's2', stepNumber: 2, sequence: 3 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1',
      blockId: 'final-answer',
      delta: '如果优先考虑用时，建议驾车。',
      runId: 'r1',
      stepId: 's2',
    }));

    expect(selectFullStreamContentBlocks(slot(s, 'c1'))).toEqual([
      { type: 'text', id: 'final-answer', text: '如果优先考虑用时，建议驾车。' },
    ]);
  });

  it('finalizeStep 保留无工具回合的正常流式回答', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1',
      blockId: 'plain-answer',
      delta: '正常回答',
      runId: 'r1',
      stepId: 's1',
    }));
    s = reducer(s, finalizeStep({ conversationId: 'c1',
      runId: 'r1',
      stepId: 's1',
      toolCallCount: 0,
      sequence: 2,
    }));

    expect(slot(s, 'c1').textBlocks['plain-answer']).toBe('正常回答');
  });

  it('markLimitReached 写 reason 不改 run.status', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, markLimitReached({ conversationId: 'c1', runId: 'r1', reason: 'max_steps', sequence: 5 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('running');
    expect(slot(s, 'c1').currentRun?.limitReachedReason).toBe('max_steps');
  });

  it('finalizeRun completed', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'completed', sequence: 99 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('completed');
  });

  it('finalizeRun limit_reached', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, markLimitReached({ conversationId: 'c1', runId: 'r1', reason: 'timeout', sequence: 5 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'limit_reached', sequence: 6 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('limit_reached');
    expect(slot(s, 'c1').currentRun?.limitReachedReason).toBe('timeout');
  });

  it('finalizeRun failed 把当前 running step 标 failed', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1',
      runId: 'r1', status: 'failed',
      failure: { code: 'X', message: 'boom' },
      sequence: 2,
    }));
    expect(slot(s, 'c1').currentRun?.status).toBe('failed');
    expect(slot(s, 'c1').currentRun?.failure?.code).toBe('X');
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('failed');
  });

  it('finalizeRun interrupted 把当前 running step 标 interrupted', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'interrupted', sequence: 2 }));
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('interrupted');
  });

  it('幂等：sequence ≤ lastSequence 时 noop', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 5 }));
    const before = s;
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's2', stepNumber: 2, sequence: 5 }));
    expect(s).toEqual(before);
  });

  it('reasoning 带 stepId 时挂到 step.contentBlockIds', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendThinkingDelta({ conversationId: 'c1',
      blockId: 'b1', delta: '思考中', runId: 'r1', stepId: 's1',
    }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toContain('b1');
  });

  it('reasoning 缺失 stepId 时只入 textBlocks 不挂 step (defensive no-op)', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendThinkingDelta({ conversationId: 'c1', blockId: 'b1', delta: '裸 thinking' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toHaveLength(0);
    expect(slot(s, 'c1').thinkingBlocks['b1']).toBe('裸 thinking');
  });

  it('startStream 清空 currentRun 和上一轮续传游标，并进入 streaming', () => {
    let s = reducer(startedAt('c2'), initRun({ conversationId: 'c2', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c2', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, setLastEntryId({ conversationId: 'c2', entryId: '99-1' }));
    s = reducer(s, setStreamStatus({ conversationId: 'c2', status: 'reconnecting' }));
    s = reducer(s, startStream({ conversationId: 'c2', messageId: 'm2' }));
    expect(slot(s, 'c2').currentRun).toBeNull();
    expect(slot(s, 'c2').lastEntryId).toBe('0');
    expect(slot(s, 'c2').streamStatus).toBe('streaming');
  });

  it('appendTextDelta 带 stepId 时挂到 step.contentBlockIds', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1',
      blockId: 'b_text', delta: 'answer', runId: 'r1', stepId: 's1',
    }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toContain('b_text');
    expect(slot(s, 'c1').textBlocks['b_text']).toBe('answer');
  });

  it('mergeToolCallDelta 不允许 BE delta 覆盖 status / toolCallId / toolName', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    // 恶意 / 未来 BE 误发 delta 包含 status/toolCallId/toolName
    s = reducer(s, mergeToolCallDelta({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1',
      delta: {
        status: 'failed',           // 应被忽略
        toolCallId: 'EVIL_ID',      // 应被忽略
        toolName: 'rogue_tool',     // 应被忽略
        startedAt: 0,               // 应被忽略
      } as Record<string, unknown>,
      sequence: 3,
    }));
    const tc = slot(s, 'c1').currentRun?.steps[0].toolCalls[0];
    expect(tc?.status).toBe('running');
    expect(tc?.toolCallId).toBe('t1');
    expect(tc?.toolName).toBe('web_search');
  });

  it('mergeToolCallDelta 允许 resultSummary / arguments 被 delta 覆盖', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    s = reducer(s, mergeToolCallDelta({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1',
      delta: {
        resultSummary: { kind: 'search', count: 3, truncated: false },
        arguments: { q: 'updated' },
      } as Record<string, unknown>,
      sequence: 3,
    }));
    const tc = slot(s, 'c1').currentRun?.steps[0].toolCalls[0];
    expect(tc?.resultSummary?.count).toBe(3);
    expect(tc?.arguments).toEqual({ q: 'updated' });
  });

  it('pushStep runId 不匹配时 noop（防 guard 被简化）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r2', stepId: 's1', stepNumber: 1, sequence: 1 }));
    expect(slot(s, 'c1').currentRun?.steps).toHaveLength(0);
    expect(slot(s, 'c1').currentRun?.runId).toBe('r1');  // 仍是原 run
  });

  it('finalizeRun runId 不匹配时 noop', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r2', status: 'completed', sequence: 99 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('running');  // 状态不变
  });

  it('initRun 同 runId 重放幂等：sequence ≤ lastSequence 时不清空 timeline', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: {}, sequence: 2,
    }));
    // 模拟重连重放 run_started(sequence=0)
    s = reducer(s, initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    // 已建 timeline 不被清空
    expect(slot(s, 'c1').currentRun?.steps).toHaveLength(1);
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls).toHaveLength(1);
  });

  it('initRun 不同 runId 时允许重建（新 run 覆盖旧）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    // 新 run 启动。messageId 跟着槽位走：换了 messageId 就是另一条流，
    // 会被 initRun 的归属判据挡掉（见「上一轮迟到的 initRun」用例）。
    s = reducer(s, initRun({ conversationId: 'c1', runId: 'r2', messageId: 'm1', config: baseConfig, sequence: 0 }));
    expect(slot(s, 'c1').currentRun?.runId).toBe('r2');
    expect(slot(s, 'c1').currentRun?.steps).toHaveLength(0);
  });

  it('totalToolCalls 跨多 step 累加（不绑定单 step.toolCalls.length）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: {}, sequence: 2,
    }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't2',
      toolName: 'url_read', arguments: {}, sequence: 3,
    }));
    s = reducer(s, finalizeStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', sequence: 4 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's2', stepNumber: 2, sequence: 5 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's2', toolCallId: 't3',
      toolName: 'web_search', arguments: {}, sequence: 6,
    }));
    expect(slot(s, 'c1').currentRun?.totalToolCalls).toBe(3);
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls).toHaveLength(2);
    expect(slot(s, 'c1').currentRun?.steps[1].toolCalls).toHaveLength(1);
  });

  it('initRun 写入 messageId 和 serverMessageId', () => {
    const s = reducer(startedAt('c1', 'local-placeholder-1'), initRun({ conversationId: 'c1',
      runId: 'r1',
      messageId: 'local-placeholder-1',
      serverMessageId: 'server-msg-uuid',
      config: baseConfig,
      sequence: 0,
    }));
    expect(slot(s, 'c1').currentRun?.messageId).toBe('local-placeholder-1');
    expect(slot(s, 'c1').currentRun?.serverMessageId).toBe('server-msg-uuid');
  });

  it('endStream 保留 currentRun（跨流生命周期）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1',
      runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0,
    }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, endStream({ conversationId: 'c1' }));
    expect(slot(s, 'c1').currentRun).not.toBeNull();
    expect(slot(s, 'c1').currentRun?.runId).toBe('r1');
    expect(slot(s, 'c1').currentRun?.steps).toHaveLength(1);
    // 但其它 streaming-only 字段应清空
    expect(slot(s, 'c1').isStreaming).toBe(false);
    expect(slot(s, 'c1').textBlocks).toEqual({});
  });

  it('两个会话各占一个槽位，结束其一不碰另一条', () => {
    // 此前全局只有一个槽位：会话 B 开流会顶掉 A，A 迟到的结束回调又会把 B 清空
    // ——B 在仍在接收时提前变成空闲（dev 验收 12:29:05 → 12:29:37）。
    // 槽位按会话拆开后这类互相踩踏在结构上就不存在了。
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-a' }));
    s = reducer(s, startStream({ conversationId: 'conv-b', messageId: 'msg-b' }));

    // B 开流不再顶掉 A
    expect(slot(s, 'conv-a').isStreaming).toBe(true);
    expect(slot(s, 'conv-b').isStreaming).toBe(true);

    s = reducer(s, endStream({ conversationId: 'conv-a', messageId: 'msg-a' }));

    expect(slot(s, 'conv-a').isStreaming).toBe(false);
    expect(slot(s, 'conv-b').isStreaming).toBe(true);
    expect(slot(s, 'conv-b').messageId).toBe('msg-b');
  });

  it('上一轮迟到的 initRun 不得覆盖新一轮的 currentRun（#74 复验 R3）', () => {
    // dev 复验：放行上一轮被扣住的响应后，旧 run 的 initRun 把 currentRun 顶掉，
    // 新一轮仍在生成却显示成 completed（14:17:29.856 currentRun 变回旧 run）。
    // 旧的"不同 runId 允许重建"是单 currentRun 设计的默认，但不能跨流生效。
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-new' }));
    s = reducer(s, initRun({ conversationId: 'conv-a',
      runId: 'run-new', messageId: 'msg-new', config: baseConfig, sequence: 0,
    }));

    s = reducer(s, initRun({ conversationId: 'conv-a',
      runId: 'run-old', messageId: 'msg-old', config: baseConfig, sequence: 0,
    }));

    expect(slot(s, 'conv-a').currentRun?.runId).toBe('run-new');
  });

  it('同一条流内不同 runId 仍可重建 currentRun', () => {
    // 归属相符时保持原有的单 currentRun 语义，不因为上面的守卫改变正常行为。
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-a' }));
    s = reducer(s, initRun({ conversationId: 'conv-a',
      runId: 'run-1', messageId: 'msg-a', config: baseConfig, sequence: 0,
    }));
    s = reducer(s, initRun({ conversationId: 'conv-a',
      runId: 'run-2', messageId: 'msg-a', config: baseConfig, sequence: 0,
    }));

    expect(slot(s, 'conv-a').currentRun?.runId).toBe('run-2');
  });

  it('同一会话里上一轮的结束回调不得清掉新一轮的流', () => {
    // 只比会话 ID 挡不住这种：两轮同属 conv-a，靠 messageId 区分。
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-1' }));
    s = reducer(s, startStream({ conversationId: 'conv-a', messageId: 'msg-2' }));

    s = reducer(s, endStream({ conversationId: 'conv-a', messageId: 'msg-1' }));

    expect(slot(s, 'conv-a').isStreaming).toBe(true);
    expect(slot(s, 'conv-a').messageId).toBe('msg-2');
  });

  it('归属相符时照常结束；不带归属仍无条件结束该会话', () => {
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-a' }));
    s = reducer(s, endStream({ conversationId: 'conv-a', messageId: 'msg-a' }));
    expect(slot(s, 'conv-a').isStreaming).toBe(false);

    s = reducer(s, startStream({ conversationId: 'conv-b', messageId: 'msg-b' }));
    s = reducer(s, endStream({ conversationId: 'conv-b' }));
    expect(slot(s, 'conv-b').isStreaming).toBe(false);
  });

  it('resetStreamState 清空所有会话的槽位（登出）', () => {
    // 按会话拆分后没有"那一条流"可以单独结束，全局失效要有自己的入口。
    let s = reducer(initial(), startStream({ conversationId: 'conv-a', messageId: 'msg-a' }));
    s = reducer(s, startStream({ conversationId: 'conv-b', messageId: 'msg-b' }));

    s = reducer(s, resetStreamState());

    expect(s.byConversation).toEqual({});
  });

  it('startStream 清空 currentRun（新轮发送不复用旧 timeline）', () => {
    let s = reducer(startedAt('c2'), initRun({ conversationId: 'c2',
      runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0,
    }));
    s = reducer(s, pushStep({ conversationId: 'c2', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, startStream({ conversationId: 'c2', messageId: 'm2' }));
    expect(slot(s, 'c2').currentRun).toBeNull();
  });

  it('continuation 旧 blocks 保留在新 delta 前', () => {
    let s = reducer(initial(), startStream({
      conversationId: 'c1',
      messageId: 'm1',
      staticBlocks: [{ type: 'text', id: 'old-text', text: '旧回答' }],
    }));

    s = reducer(s, appendTextDelta({ conversationId: 'c1',
      blockId: 'new-text',
      delta: '新补充',
      runId: 'r2',
      stepId: 's1',
    }));

    expect(selectFullStreamContentBlocks(slot(s, 'c1'))).toEqual([
      { type: 'text', id: 'old-text', text: '旧回答' },
      { type: 'text', id: 'new-text', text: '新补充' },
    ]);
  });
});

describe('streamSlice — interrupted 派生（contract §3）', () => {
  it('finalizeRun(status=interrupted) 把 running step 派生为 interrupted', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'interrupted', sequence: 2 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('interrupted');
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('interrupted');
    expect(slot(s, 'c1').currentRun?.steps[0].completedAt).toBeGreaterThan(0);
  });

  it('finalizeRun(status=interrupted) 把 running tool call 派生为 interrupted', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't2',
      toolName: 'url_read', arguments: { url: 'https://x' }, sequence: 3,
    }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'interrupted', sequence: 4 }));
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('interrupted');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[1].status).toBe('interrupted');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].completedAt).toBeGreaterThan(0);
  });

  it('finalizeRun(status=interrupted) 不影响已完成的 step / tool call', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1', status: 'success', durationMs: 10, sequence: 3,
    }));
    s = reducer(s, finalizeStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', sequence: 4 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's2', stepNumber: 2, sequence: 5 }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'interrupted', sequence: 6 }));
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('completed');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('success');
    expect(slot(s, 'c1').currentRun?.steps[1].status).toBe('interrupted');
  });

  it('finalizeRun(status=completed) 不派生 interrupted（防御性回归测试）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    // 此时 step + tool call 都是 running，run 直接 completed（不太合理但要防御）
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'completed', sequence: 3 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('completed');
    // step / tool call 应保持 running 不被派生为 interrupted
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('running');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('running');
  });

  // codex review: failed 路径之前只标 lastStep，不扫 running tool call，
  // tool 在 started 之后失败时 chip 会一直转。回归测：finalizeRun(failed) 后
  // 所有 running tool call 必须被标为 failed + completedAt 写入。
  it('finalizeRun(status=failed) 把 running step 和 running tool call 都派生为 failed', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't2',
      toolName: 'url_read', arguments: { url: 'https://x' }, sequence: 3,
    }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'failed', sequence: 4 }));
    expect(slot(s, 'c1').currentRun?.status).toBe('failed');
    expect(slot(s, 'c1').currentRun?.steps[0].status).toBe('failed');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('failed');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[1].status).toBe('failed');
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].completedAt).toBeGreaterThan(0);
  });

  it('finalizeRun(status=failed) 不影响已完成的 tool call（只改 running 的）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't1',
      toolName: 'web_search', arguments: { q: 'x' }, sequence: 2,
    }));
    s = reducer(s, finalizeToolCall({ conversationId: 'c1',
      runId: 'r1', toolCallId: 't1', status: 'success', durationMs: 10, sequence: 3,
    }));
    s = reducer(s, pushToolCall({ conversationId: 'c1',
      runId: 'r1', stepId: 's1', toolCallId: 't2',
      toolName: 'url_read', arguments: { url: 'https://x' }, sequence: 4,
    }));
    s = reducer(s, finalizeRun({ conversationId: 'c1', runId: 'r1', status: 'failed', sequence: 5 }));
    // t1 已 success，不应被改成 failed
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[0].status).toBe('success');
    // t2 还在 running，应被派生为 failed
    expect(slot(s, 'c1').currentRun?.steps[0].toolCalls[1].status).toBe('failed');
  });
});

describe('streamSlice — contentBlockIds 关联（contract §6.5 defensive）', () => {
  it('appendTextDelta 首次 delta 带 stepId 时挂 contentBlockIds', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: 'hello', runId: 'r1', stepId: 's1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual(['blk_1']);
  });

  it('appendTextDelta 首次 delta 不带 stepId，后续 delta 带 stepId 也能挂 contentBlockIds（关键 bug 修复）', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    // 第一次 delta 不带 stepId
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: 'hello', runId: 'r1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual([]);
    // 第二次 delta 带 stepId
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: ' world', runId: 'r1', stepId: 's1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual(['blk_1']);
  });

  it('appendTextDelta 多次带 stepId 不会重复挂 contentBlockIds', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: 'a', runId: 'r1', stepId: 's1' }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: 'b', runId: 'r1', stepId: 's1' }));
    s = reducer(s, appendTextDelta({ conversationId: 'c1', blockId: 'blk_1', delta: 'c', runId: 'r1', stepId: 's1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual(['blk_1']);
  });

  it('appendThinkingDelta 首次 delta 不带 stepId，后续带 stepId 也能挂 contentBlockIds', () => {
    let s = reducer(startedAt('c1'), initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
    s = reducer(s, pushStep({ conversationId: 'c1', runId: 'r1', stepId: 's1', stepNumber: 1, sequence: 1 }));
    s = reducer(s, appendThinkingDelta({ conversationId: 'c1', blockId: 'blk_t', delta: '...', runId: 'r1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual([]);
    s = reducer(s, appendThinkingDelta({ conversationId: 'c1', blockId: 'blk_t', delta: '...', runId: 'r1', stepId: 's1' }));
    expect(slot(s, 'c1').currentRun?.steps[0].contentBlockIds).toEqual(['blk_t']);
  });
});
