import { createSelector, createSlice, PayloadAction } from '@reduxjs/toolkit';
import type {
  ContentBlock,
  ContextUsage,
  KnowledgeEvidenceBlock,
  SearchSourceSummary,
  StructuredToolResultBlock,
} from '@/types/conversation';
import type { ContextUsagePhase } from '@/lib/chat/contextUsage';
import { logout } from '@/redux/slices/authSlice';
import { accountSessionSwitchStarted } from '@/redux/actions/authSessionActions';
import type {
  AgentEvidenceItem,
  AgentPlanItem,
  AgentPlanMode,
  AgentPlanSource,
  AgentPlanState,
  AgentProgressState,
  AgentRunConfig,
  AgentRunState,
  AgentRunStatus,
  AgentToolDigest,
  AgentContextPurpose,
  AgentContextRequestPhase,
  AgentContextResultStatus,
  AgentContextType,
  PendingAgentContextRequest,
  LimitReachedReason,
  ToolCallResultSummary,
} from '@/types/agentRun';
import {
  normalizeAgentPlanItem,
  normalizeAgentPlanMetadata,
  normalizeAgentPlanState,
} from '@/lib/agent/planState';

export interface StreamSlot {
  // ── 流元信息 ──
  conversationId: string | null;
  messageId: string | null;
  staticBlocks: ContentBlock[];
  // ── block 增量（保留：reasoning/answering 仍走这里）──
  textBlocks: Record<string, string>;
  thinkingBlocks: Record<string, string>;
  blockOrder: string[];
  blockTypes: Record<string, 'text' | 'thinking'>;
  totalTextLength: number;
  displayedTextLength: number;
  // ── 流状态枚举 ──
  isStreaming: boolean;
  isStreamingReasoning: boolean;
  isThinkingPhaseComplete: boolean;
  reasoningStartTime: number | null;
  reasoningEndTime: number | undefined;
  // ── 来源（保留：Markdown 引用与回答依据仍读）──
  // 注：Phase 1 cut over 后 streaming 期 searchSources 不再被 reducer 主动填充；
  // 由 ChatMessage (Task 13b) 在 stream 结束后从消息 ContentBlock 提取
  searchSources: SearchSourceSummary[];
  // ── 断线重连 ──
  lastEntryId: string;
  streamStatus: 'idle' | 'streaming' | 'reconnecting' | 'completed' | 'error';
  // ── Agent run timeline（Task 12 新增）──
  currentRun: AgentRunState | null;
  // ── 错误卡片 ──
  lastError: { message: string; code?: string; data?: Record<string, unknown> } | null;
  contextUsage: ContextUsage | null;
  contextUsageConversationId: string | null;
  contextUsageMeta: {
    runId: string;
    messageId: string;
    sequence: number;
    phase: ContextUsagePhase;
    roundIndex: number | null;
  } | null;
  contextUsageInFlight: ContextUsage | null;
  contextUsageInFlightConversationId: string | null;
  contextUsageInFlightMeta: {
    runId: string;
    messageId: string;
    sequence: number;
    phase: ContextUsagePhase;
    roundIndex: number | null;
  } | null;
  pendingContextRequest: PendingAgentContextRequest | null;
}

export const EMPTY_STREAM_SLOT: StreamSlot = {
  conversationId: null,
  messageId: null,
  staticBlocks: [],
  textBlocks: {},
  thinkingBlocks: {},
  blockOrder: [],
  blockTypes: {},
  totalTextLength: 0,
  displayedTextLength: 0,
  isStreaming: false,
  isStreamingReasoning: false,
  isThinkingPhaseComplete: false,
  reasoningStartTime: null,
  reasoningEndTime: undefined,
  searchSources: [],
  lastEntryId: '0',
  streamStatus: 'idle',
  currentRun: null,
  lastError: null,
  contextUsage: null,
  contextUsageConversationId: null,
  contextUsageMeta: null,
  contextUsageInFlight: null,
  contextUsageInFlightConversationId: null,
  contextUsageInFlightMeta: null,
  pendingContextRequest: null,
};

/** 按会话索引的流槽位。
 *
 * 此前整个应用只有一个流槽位：`conversationId` / `isStreaming` / `messageId` /
 * `textBlocks` 都是单份的，第二条流一开始就会覆盖第一条。后端本来就支持并发
 * （dev 实测两个 run 重叠 20 秒、均 200、无踢流），限制完全在前端这一层。
 *
 * 槽位在 startStream 建立，endStream 只清空不删除——错误卡片、Agent 摘要和
 * 上下文用量在流结束后仍要读（见 endStream 保留的那几个字段）。
 */
export interface StreamState {
  byConversation: Record<string, StreamSlot>;
}

const initialState: StreamState = { byConversation: {} };

/** 非流式槽位的保留上限。留尾很小（错误卡片 / run 摘要 / 用量），
 * 但没有上限就会随浏览会话数无限增长。超出时按插入顺序淘汰最旧的非流式槽位。 */
const MAX_RETAINED_IDLE_SLOTS = 20;

function pruneIdleSlots(state: StreamState): void {
  const idle = Object.keys(state.byConversation)
    .filter(id => !state.byConversation[id].isStreaming);
  const overflow = idle.length - MAX_RETAINED_IDLE_SLOTS;
  for (let i = 0; i < overflow; i += 1) {
    delete state.byConversation[idle[i]];
  }
}

/** 把 reducer 限定在某个会话的槽位上：函数体照旧写 `state.xxx`，只是这个 state
 * 现在是该会话的槽位而不是全局单槽。槽位不存在（流已结束、事件迟到）时直接丢弃。 */
function withSlot<P extends { conversationId: string }>(
  body: (slot: StreamSlot, action: PayloadAction<P>) => void,
) {
  return (state: StreamState, action: PayloadAction<P>) => {
    // 没带会话 ID 的槽位 action 谁也不指向，直接丢弃——reducer 不该被野生 action 打挂。
    if (!action.payload?.conversationId) return;
    const slot = state.byConversation[action.payload.conversationId];
    if (!slot) return;
    body(slot, action);
  };
}

const MAX_EVIDENCE_ITEMS = 12;

function evidencePriority(item: AgentEvidenceItem): number {
  if (item.status === 'used' || item.usedByFinalAnswer) return 5;
  if (item.status === 'read_success') return 4;
  if (item.status === 'selected') return 3;
  if (item.status === 'read_degraded' || item.status === 'read_failed') return 2;
  return 1;
}

function mergeEvidenceItems(
  existing: AgentEvidenceItem,
  incoming: AgentEvidenceItem,
): AgentEvidenceItem {
  const existingPriority = evidencePriority(existing);
  const incomingPriority = evidencePriority(incoming);
  const primary = incomingPriority >= existingPriority ? incoming : existing;
  const secondary = primary === incoming ? existing : incoming;
  const primaryRecord = primary as unknown as Record<string, unknown>;
  const secondaryRecord = secondary as unknown as Record<string, unknown>;
  const merged: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(primaryRecord)) {
    merged[key] = value !== null && value !== undefined && value !== ''
      ? value
      : secondaryRecord[key];
  }
  for (const [key, value] of Object.entries(secondaryRecord)) {
    if (!(key in merged)) {
      merged[key] = value;
    }
  }

  return merged as unknown as AgentEvidenceItem;
}

function capEvidenceItems(items: AgentEvidenceItem[]): AgentEvidenceItem[] {
  if (items.length <= MAX_EVIDENCE_ITEMS) return items;

  const kept = items
    .map((item, index) => ({ item, index }))
    .sort((left, right) => (
      evidencePriority(right.item) - evidencePriority(left.item)
      || right.index - left.index
    ))
    .slice(0, MAX_EVIDENCE_ITEMS)
    .sort((left, right) => left.index - right.index);
  return kept.map(entry => entry.item);
}

const streamSlice = createSlice({
  name: 'stream',
  initialState,
  reducers: {
    startStream(
      state,
      action: PayloadAction<{ conversationId: string; messageId: string; staticBlocks?: ContentBlock[] }>
    ) {
      // 唯一建立槽位的入口。同一会话重新发送时整槽重置，语义与此前一致；
      // 其他会话的槽位不受影响，这正是本次改造要的并发。
      const slot: StreamSlot = { ...EMPTY_STREAM_SLOT };
      state.byConversation[action.payload.conversationId] = slot;
      pruneIdleSlots(state);
      slot.conversationId = action.payload.conversationId;
      slot.messageId = action.payload.messageId;
      slot.staticBlocks = action.payload.staticBlocks ?? [];
      slot.textBlocks = {};
      slot.thinkingBlocks = {};
      slot.blockOrder = [];
      slot.blockTypes = {};
      slot.totalTextLength = 0;
      slot.displayedTextLength = 0;
      slot.isStreaming = true;
      slot.isStreamingReasoning = false;
      slot.isThinkingPhaseComplete = false;
      slot.reasoningStartTime = null;
      slot.reasoningEndTime = undefined;
      slot.searchSources = [];
      slot.lastEntryId = '0';
      slot.streamStatus = 'streaming';
      slot.currentRun = null;
      // 新一轮发送清空上一次的错误卡片
      slot.lastError = null;
      slot.contextUsage = null;
      slot.contextUsageConversationId = action.payload.conversationId;
      slot.contextUsageMeta = null;
      slot.contextUsageInFlight = null;
      slot.contextUsageInFlightConversationId = action.payload.conversationId;
      slot.contextUsageInFlightMeta = null;
      slot.pendingContextRequest = null;
    },

    appendTextDelta: withSlot((state, action: PayloadAction<{ conversationId: string } & { blockId: string; delta: string; runId?: string; stepId?: string }>) => {
      const { blockId, delta, runId, stepId } = action.payload;
      // 首次创建：注册 block type + order
      if (!state.blockTypes[blockId]) {
        state.blockTypes[blockId] = 'text';
        state.blockOrder.push(blockId);
      }
      // 每次 delta 都尝试关联 step（runId/stepId 是 optional，可能首次 delta 没带后续 delta 带了）
      // includes 防重复挂，spec §6.5 defensive no-op
      if (runId && stepId && state.currentRun?.runId === runId) {
        const step = state.currentRun.steps.find(s => s.stepId === stepId);
        if (step && !step.contentBlockIds.includes(blockId)) {
          step.contentBlockIds.push(blockId);
        }
      }
      state.textBlocks[blockId] = (state.textBlocks[blockId] ?? '') + delta;
      state.totalTextLength += delta.length;
    }),

    appendThinkingDelta: withSlot((state, action: PayloadAction<{ conversationId: string } & { blockId: string; delta: string; runId?: string; stepId?: string }>) => {
      const { blockId, delta, runId, stepId } = action.payload;
      if (!state.blockTypes[blockId]) {
        state.blockTypes[blockId] = 'thinking';
        state.blockOrder.push(blockId);
        if (!state.isStreamingReasoning) {
          state.isStreamingReasoning = true;
          state.reasoningStartTime = Date.now();
        }
      }
      // 每次 delta 都尝试关联 step（runId/stepId 是 optional，可能首次 delta 没带后续 delta 带了）
      // includes 防重复挂，spec §6.5 defensive no-op
      if (runId && stepId && state.currentRun?.runId === runId) {
        const step = state.currentRun.steps.find(s => s.stepId === stepId);
        if (step && !step.contentBlockIds.includes(blockId)) {
          step.contentBlockIds.push(blockId);
        }
      }
      state.thinkingBlocks[blockId] = (state.thinkingBlocks[blockId] ?? '') + delta;
    }),

    discardContentBlock: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; blockId: string; sequence: number }>) => {
      const run = state.currentRun;
      const { runId, blockId, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      if (state.blockTypes[blockId] === 'text') {
        state.totalTextLength = Math.max(
          0,
          state.totalTextLength - (state.textBlocks[blockId]?.length ?? 0),
        );
        state.displayedTextLength = Math.min(state.displayedTextLength, state.totalTextLength);
        delete state.textBlocks[blockId];
        delete state.blockTypes[blockId];
        state.blockOrder = state.blockOrder.filter(existingId => existingId !== blockId);
      }
      state.staticBlocks = state.staticBlocks.filter(block => block.id !== blockId);
      for (const step of run.steps) {
        step.contentBlockIds = step.contentBlockIds.filter(existingId => existingId !== blockId);
      }
    }),

    // 打字机每 tick 推进显示长度
    advanceTypewriter: withSlot((state, action: PayloadAction<{ conversationId: string; chars: number }>) => {
      state.displayedTextLength = Math.min(
        state.displayedTextLength + action.payload.chars,
        state.totalTextLength
      );
    }),

    completeThinkingPhase: withSlot<{ conversationId: string }>((state) => {
      state.isThinkingPhaseComplete = true;
      state.isStreamingReasoning = false;
      state.reasoningEndTime = Date.now();
    }),

    /** 草稿会话转正：把槽位整体迁到服务端会话 ID 下。
     *
     * 槽位现在以会话 ID 为键，所以迁移的是键本身——留在旧键下的槽位既查不到、
     * 也再收不到后续事件。与 streamControllerRegistry 的 migrateStreamController 对应。
     */
    migrateStreamConversation(
      state,
      action: PayloadAction<{ from: string; to: string }>,
    ) {
      const { from, to } = action.payload;
      if (from === to) return;
      const slot = state.byConversation[from];
      if (!slot) return;
      delete state.byConversation[from];
      state.byConversation[to] = slot;
      slot.conversationId = to;
      if (slot.contextUsageConversationId === from) {
        slot.contextUsageConversationId = to;
      }
      if (slot.contextUsageInFlightConversationId === from) {
        slot.contextUsageInFlightConversationId = to;
      }
      if (slot.pendingContextRequest?.conversationId === from) {
        slot.pendingContextRequest.conversationId = to;
      }
    },

    setLastEntryId: withSlot((state, action: PayloadAction<{ conversationId: string; entryId: string }>) => {
      state.lastEntryId = action.payload.entryId;
    }),

    // ── Agent run timeline reducers (Task 12 / spec §6.4) ──

    receiveContextRequired: withSlot((state, action: PayloadAction<{ conversationId: string } & { conversationId: string; runId: string; requestId: string; contextType: AgentContextType; purpose: AgentContextPurpose; reason: string; expiresAt: number; sequence: number; }>) => {
      const run = state.currentRun;
      const { conversationId, runId, requestId, contextType, purpose, reason, expiresAt, sequence } = action.payload;
      if (
        !state.isStreaming
        || state.conversationId !== conversationId
        || !run
        || run.runId !== runId
        || sequence <= run.lastSequence
      ) return;
      run.lastSequence = sequence;
      state.pendingContextRequest = {
        conversationId,
        runId,
        requestId,
        contextType,
        purpose,
        reason,
        expiresAt,
        sequence,
        phase: 'required',
      };
    }),

    setContextRequestPhase: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; requestId: string; phase: AgentContextRequestPhase; }>) => {
      const request = state.pendingContextRequest;
      if (
        !request
        || request.runId !== action.payload.runId
        || request.requestId !== action.payload.requestId
      ) return;
      request.phase = action.payload.phase;
    }),

    receiveContextResult: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; requestId: string; contextType: AgentContextType; status: AgentContextResultStatus; sequence: number; }>) => {
      const run = state.currentRun;
      const { runId, requestId, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      const request = state.pendingContextRequest;
      if (request?.runId === runId && request.requestId === requestId) {
        state.pendingContextRequest = null;
      }
    }),

    initRun: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; messageId: string; serverMessageId?: string; config: AgentRunConfig; sequence: number; }>) => {
      const { runId, messageId, serverMessageId, config, sequence } = action.payload;
      // 归属校验：单 currentRun 是同一条流内的设计，不能跨流生效。上一轮迟到的 initRun
      // 会把新一轮的 currentRun 顶掉，新一轮仍在生成却显示成已完成（dev 复验 14:17:29.856）。
      // 与 endStream 同一套判据，理由见那里。
      if (state.messageId !== null && state.messageId !== messageId) return;
      // 跨重连幂等：同 runId 且 sequence 已应用 → noop（防重放清空已建 timeline）
      // 同一条流内不同 runId 仍允许重建（新 run 覆盖旧 run timeline，spec §6.2 单 currentRun 设计）
      if (
        state.currentRun &&
        state.currentRun.runId === runId &&
        sequence <= state.currentRun.lastSequence
      ) {
        return;
      }
      state.currentRun = {
        runId,
        messageId,
        serverMessageId,
        status: 'running',
        config,
        totalSteps: 0,
        totalToolCalls: 0,
        steps: [],
        evidence: [],
        toolDigests: [],
        lastSequence: sequence,
      };
    }),

    updateRunProgress: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; progress: AgentProgressState }>) => {
      const run = state.currentRun;
      const { runId, sequence, progress } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      run.progress = progress;
    }),

    applyPlanSnapshot: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; plan: AgentPlanState }>) => {
      const run = state.currentRun;
      const { runId, sequence, plan } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      if (
        run.plan
        && run.plan.planId === plan.planId
        && plan.revision <= run.plan.revision
      ) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      run.plan = normalizeAgentPlanState(plan);
    }),

    updatePlanStep: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; planId: string; revision: number; mode?: AgentPlanMode; source?: AgentPlanSource; reason?: string; item: AgentPlanItem; }>) => {
      const run = state.currentRun;
      const { runId, sequence, planId, revision, item, mode, source, reason } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      if (!run.plan || run.plan.planId !== planId || revision <= run.plan.revision) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      const normalizedItem = normalizeAgentPlanItem(item);
      const index = run.plan.items.findIndex(existing => existing.id === item.id);
      if (index >= 0) {
        run.plan.items[index] = normalizedItem;
      } else {
        run.plan.items.push(normalizedItem);
      }
      run.plan.revision = revision;
      if (mode !== undefined || source !== undefined || reason !== undefined) {
        Object.assign(run.plan, normalizeAgentPlanMetadata({ mode, source, reason }));
      }
    }),

    upsertToolDigest: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; digest: AgentToolDigest }>) => {
      const run = state.currentRun;
      const { runId, sequence, digest } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      run.toolDigests = run.toolDigests ?? [];
      if (digest.repairId) {
        run.toolDigests = run.toolDigests.filter(existing => existing.repairId !== digest.repairId);
      }
      const index = run.toolDigests.findIndex(existing => existing.toolCallId === digest.toolCallId);
      if (index >= 0) {
        const existingPlanItemId = run.toolDigests[index].planItemId;
        run.toolDigests[index] = (
          digest.planItemId === undefined && existingPlanItemId !== undefined
            ? { ...digest, planItemId: existingPlanItemId }
            : digest
        );
      } else {
        run.toolDigests.push(digest);
      }
    }),

    upsertEvidenceItem: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; evidence: AgentEvidenceItem }>) => {
      const run = state.currentRun;
      const { runId, sequence, evidence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      run.evidence = run.evidence ?? [];
      const index = run.evidence.findIndex(existing => existing.id === evidence.id);
      if (index >= 0) {
        run.evidence[index] = mergeEvidenceItems(run.evidence[index], evidence);
      } else {
        run.evidence.push(evidence);
      }
      run.evidence = capEvidenceItems(run.evidence);
    }),

    upsertStaticContentBlock: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; sequence: number; block: StructuredToolResultBlock | KnowledgeEvidenceBlock; }>) => {
      const run = state.currentRun;
      const { runId, sequence, block } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.protocolVersion = 2;
      const index = state.staticBlocks.findIndex(existing => existing.id === block.id);
      if (index >= 0) {
        state.staticBlocks[index] = block;
      } else {
        state.staticBlocks.push(block);
      }
    }),

    pushStep: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; stepId: string; stepNumber: number; sequence: number }>) => {
      const run = state.currentRun;
      const { runId, stepId, stepNumber, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.steps.push({
        stepId,
        stepNumber,
        status: 'running',
        toolCalls: [],
        contentBlockIds: [],
        startedAt: Date.now(),
      });
      run.totalSteps = Math.max(run.totalSteps, stepNumber);
    }),

    pushToolCall: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; stepId: string; toolCallId: string; planItemId?: string; toolName: string; arguments: Record<string, unknown>; sequence: number; }>) => {
      const run = state.currentRun;
      const {
        runId,
        stepId,
        toolCallId,
        planItemId,
        toolName,
        arguments: args,
        sequence,
      } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      const step = run.steps.find(s => s.stepId === stepId);
      if (!step) return; // defensive: step 应该已存在
      step.toolCalls.push({
        toolCallId,
        ...(planItemId ? { planItemId } : {}),
        toolName,
        arguments: args,
        status: 'running',
        startedAt: Date.now(),
      });
      run.totalToolCalls += 1;
    }),

    mergeToolCallDelta: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; toolCallId: string; delta: Record<string, unknown>; sequence: number; }>) => {
      const run = state.currentRun;
      const { runId, toolCallId, delta, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      for (const step of run.steps) {
        const tc = step.toolCalls.find(t => t.toolCallId === toolCallId);
        if (tc) {
          // 显式 allowlist：仅 resultSummary / arguments 可被 delta 覆盖。
          // 不允许 status / toolCallId / toolName / startedAt / completedAt 被 delta 改，
          // 它们由 finalizeToolCall / pushToolCall 等专用 reducer 控制。
          const tcRecord = tc as unknown as Record<string, unknown>;
          if ('resultSummary' in delta) {
            tcRecord.resultSummary = delta.resultSummary;
          }
          if ('arguments' in delta) {
            tcRecord.arguments = delta.arguments;
          }
          return;
        }
      }
    }),

    finalizeToolCall: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; toolCallId: string; planItemId?: string; status: 'success' | 'failed' | 'degraded'; durationMs: number; resultSummary?: ToolCallResultSummary; error?: string | null; sequence: number; }>) => {
      const run = state.currentRun;
      const {
        runId,
        toolCallId,
        planItemId,
        status,
        durationMs,
        resultSummary,
        error,
        sequence,
      } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      for (const step of run.steps) {
        const tc = step.toolCalls.find(t => t.toolCallId === toolCallId);
        if (tc) {
          if (planItemId) tc.planItemId = planItemId;
          tc.status = status;
          tc.completedAt = Date.now();
          if (resultSummary) tc.resultSummary = resultSummary;
          if (error) tc.error = error;
          const resolvedRepairId = resultSummary?.resolves_repair_id;
          if (status === 'success' && resolvedRepairId) {
            run.toolDigests = (run.toolDigests ?? []).filter(
              digest => digest.repairId !== resolvedRepairId,
            );
            for (const existingStep of run.steps) {
              for (const existingCall of existingStep.toolCalls) {
                if (existingCall.resultSummary?.repair_id !== resolvedRepairId) continue;
                existingCall.status = 'success';
                existingCall.resultSummary = {
                  ...existingCall.resultSummary,
                  repair_state: 'resolved',
                };
                delete existingCall.error;
              }
            }
          }
          // duration_ms 暂不映射到 ToolCallState（信息可由 startedAt/completedAt 推算）
          void durationMs;
          return;
        }
      }
    }),

    finalizeStep: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; stepId: string; sequence: number; toolCallCount?: number; }>) => {
      const run = state.currentRun;
      const { runId, stepId, sequence, toolCallCount } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      const step = run.steps.find(s => s.stepId === stepId);
      if (step) {
        if ((toolCallCount ?? step.toolCalls.length) > 0) {
          const transientTextIds = new Set(
            step.contentBlockIds.filter(blockId => state.blockTypes[blockId] === 'text'),
          );
          for (const blockId of transientTextIds) {
            state.totalTextLength -= state.textBlocks[blockId]?.length ?? 0;
            delete state.textBlocks[blockId];
            delete state.blockTypes[blockId];
          }
          state.totalTextLength = Math.max(0, state.totalTextLength);
          state.displayedTextLength = Math.min(state.displayedTextLength, state.totalTextLength);
          state.blockOrder = state.blockOrder.filter(blockId => !transientTextIds.has(blockId));
          step.contentBlockIds = step.contentBlockIds.filter(blockId => !transientTextIds.has(blockId));
        }
        step.status = 'completed';
        step.completedAt = Date.now();
      }
    }),

    markLimitReached: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; reason: LimitReachedReason; sequence: number }>) => {
      const run = state.currentRun;
      const { runId, reason, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.limitReachedReason = reason;
      // 不改 run.status —— spec §4.3，run_limit_reached 是信号事件，仅 run_completed 才写终态
    }),

    setRunStopConfirmation: withSlot((state, action: PayloadAction<{
      conversationId: string;
      runId: string;
      confirmation: NonNullable<AgentRunState['stopConfirmation']>;
    }>) => {
      const run = state.currentRun;
      if (run?.runId !== action.payload.runId || run.status !== 'running') return;
      run.stopConfirmation = action.payload.confirmation;
    }),

    finalizeRun: withSlot((state, action: PayloadAction<{ conversationId: string } & { runId: string; status: Exclude<AgentRunStatus, 'running'>; failure?: { code: string; message: string }; reason?: string; sequence: number; }>) => {
      const run = state.currentRun;
      const { runId, status, failure, sequence } = action.payload;
      if (!run || run.runId !== runId || sequence <= run.lastSequence) return;
      run.lastSequence = sequence;
      run.status = status;
      delete run.stopConfirmation;
      if (failure) run.failure = failure;
      if (state.pendingContextRequest?.runId === runId) {
        state.pendingContextRequest = null;
      }
      // contract §3：run-level 终态（interrupted/failed）派生——
      //   扫所有 running step 和 tool call，把它们标为对应终态，避免 UI 残留 spinner。
      //   只改 running 的，不动已 completed/failed/degraded 的历史 tool call。
      //   失败发生在 tool_call_started 之后、tool_call_completed 之前时，BE 不会再
      //   补 tool_call_completed，FE 必须在这里收尾，否则 chip 会一直转。
      if (status === 'interrupted' || status === 'failed') {
        const now = Date.now();
        run.steps.forEach(step => {
          if (step.status === 'running') {
            step.status = status;
            step.completedAt = now;
          }
          step.toolCalls?.forEach(tc => {
            if (tc.status === 'running') {
              tc.status = status;
              tc.completedAt = now;
            }
          });
        });
      }
    }),

    setStreamStatus: withSlot((state, action: PayloadAction<{ conversationId: string; status: StreamSlot['streamStatus'] }>) => {
      state.streamStatus = action.payload.status;
    }),

    setStreamError: withSlot((state, action: PayloadAction<{ conversationId: string } & { message: string; code?: string; data?: Record<string, unknown> }>) => {
      // 只留错误本身：conversationId 是路由用的，不该混进错误卡片的数据里。
      const { message, code, data } = action.payload;
      state.lastError = { message, code, data };
    }),

    clearStreamError: withSlot<{ conversationId: string }>((state) => {
      state.lastError = null;
    }),

    updateContextUsage: withSlot((state, action: PayloadAction<{ conversationId: string } & { conversationId: string; usage: ContextUsage; runId: string; messageId: string; sequence: number; phase: ContextUsagePhase; }>) => {
      const { conversationId, usage, runId, messageId, sequence, phase } = action.payload;
      if (state.conversationId !== conversationId || !state.isStreaming) return;
      if (state.currentRun && state.currentRun.runId !== runId) return;
      if (
        state.currentRun?.serverMessageId
        && state.currentRun.serverMessageId !== messageId
      ) return;

      const previous = state.contextUsageInFlightMeta;
      if (previous?.runId === runId && previous.messageId === messageId) {
        if (sequence <= previous.sequence) return;
        const sameRound = previous.roundIndex === usage.round_index;
        if (
          previous.roundIndex !== null
          && usage.round_index !== null
          && usage.round_index < previous.roundIndex
        ) return;
        if (sameRound && previous.phase === 'final' && phase !== 'final') return;
        if (sameRound && previous.phase === 'error' && phase !== 'error') return;
      }

      const nextMeta = {
        runId,
        messageId,
        sequence,
        phase,
        roundIndex: usage.round_index,
      };
      state.contextUsageInFlight = usage;
      state.contextUsageInFlightConversationId = conversationId;
      state.contextUsageInFlightMeta = nextMeta;

      if (phase === 'final') {
        if (usage.actual_prompt_tokens !== null) {
          state.contextUsage = usage;
          state.contextUsageConversationId = conversationId;
          state.contextUsageMeta = nextMeta;
        }
      }
    }),

    endStream: withSlot((state, action: PayloadAction<{ conversationId: string; messageId?: string | null }>) => {
      // 归属校验：全局槽位同一时刻只装一条流，而结束回调可能迟到。会话 A 的回调晚到时，
      // 槽位可能已被会话 B 的恢复流占用；无条件清空会让 B 在仍在接收时提前变成空闲
      // （dev 验收 12:29:05 提前 idle、12:29:37 正文才到）。同一会话的上一轮回调同理，
      // 所以按 messageId 比对而不是 conversationId：messageId 在 startStream 后不再变更，
      // conversationId 还会因草稿会话转正被 migrateStreamConversation 迁移。
      // 不带归属的调用（登出、切会话清理）保持无条件清空。
      // messageId 为 null 表示调用方这一轮还没拿到身份，退回旧的无条件语义，不引入新行为。
      const owner = action.payload.messageId ?? null;
      if (owner !== null && state.messageId !== null && state.messageId !== owner) return;
      // 保留 lastError 跨流生命周期：错误卡片需要在 endStream 后继续显示，
      // 由 startStream（新一轮发送）或 clearStreamError（用户手动 dismiss）清掉
      // 保留 currentRun 跨流生命周期：AgentStepCard 在流结束后显示折叠摘要
      //   （由 startStream 新一轮发送清掉，或由 ChatMessage 用 messageId 过滤
      //    确保不挂错对话）
      const preservedError = state.lastError;
      const preservedRun = state.currentRun;
      const preservedContextUsage = state.contextUsage;
      const preservedContextUsageConversationId = state.contextUsageConversationId;
      const preservedContextUsageMeta = state.contextUsageMeta;
      const preservedContextUsageInFlight = state.contextUsageInFlight;
      const preservedContextUsageInFlightConversationId = state.contextUsageInFlightConversationId;
      const preservedContextUsageInFlightMeta = state.contextUsageInFlightMeta;
      Object.assign(state, EMPTY_STREAM_SLOT);
      state.lastError = preservedError;
      state.currentRun = preservedRun;
      state.contextUsage = preservedContextUsage;
      state.contextUsageConversationId = preservedContextUsageConversationId;
      state.contextUsageMeta = preservedContextUsageMeta;
      state.contextUsageInFlight = preservedContextUsageInFlight;
      state.contextUsageInFlightConversationId = preservedContextUsageInFlightConversationId;
      state.contextUsageInFlightMeta = preservedContextUsageInFlightMeta;
    }),

    /** 清空所有会话的槽位。登出等全局失效场景用；
     *  按会话拆分后已经没有"那一条流"可以单独结束了。 */
    resetStreamState(state) {
      state.byConversation = {};
    },

    clearCurrentRun: withSlot<{ conversationId: string }>((state) => {
      state.currentRun = null;
    }),
  },
  extraReducers: builder => {
    builder.addCase(logout, () => initialState);
    builder.addCase(accountSessionSwitchStarted, () => initialState);
  },
});

/** 取某个会话的流槽位；没有则返回共享的空槽位。
 *
 * 返回的是稳定引用（EMPTY_STREAM_SLOT 本身），所以「没有流」的会话不会每次
 * 取值都拿到新对象而触发重渲染。**空槽位不可写**，只用于读。
 */
export function selectStreamSlot(
  state: { stream: StreamState },
  conversationId: string | null | undefined,
): StreamSlot {
  if (!conversationId) return EMPTY_STREAM_SLOT;
  return state.stream?.byConversation?.[conversationId] ?? EMPTY_STREAM_SLOT;
}

/** 当前正在生成的所有会话 ID。侧边栏据此为每个会话各自转圈——
 * 此前只能显示一个，因为全局只有一个 conversationId。 */
const EMPTY_STREAMING_CONVERSATION_IDS: string[] = [];

export function selectStreamingConversationIds(state: { stream: StreamState }): string[] {
  const byConversation = state.stream?.byConversation;
  if (!byConversation) return EMPTY_STREAMING_CONVERSATION_IDS;
  return Object.keys(byConversation).filter(id => byConversation[id].isStreaming);
}

// Selector：从 streamSlice 组装出当前流式 content blocks 数组
// thinking blocks 全量返回（实时显示），text blocks 按 displayedTextLength 截断（打字机效果）
type StreamContentBlockSelectorArgs = [
  StreamSlot['staticBlocks'],
  StreamSlot['textBlocks'],
  StreamSlot['thinkingBlocks'],
  StreamSlot['blockOrder'],
  StreamSlot['blockTypes'],
  StreamSlot['displayedTextLength'],
];

const selectStreamContentBlocksFromParts = createSelector(
  [
    (...args: StreamContentBlockSelectorArgs) => args[0],
    (...args: StreamContentBlockSelectorArgs) => args[1],
    (...args: StreamContentBlockSelectorArgs) => args[2],
    (...args: StreamContentBlockSelectorArgs) => args[3],
    (...args: StreamContentBlockSelectorArgs) => args[4],
    (...args: StreamContentBlockSelectorArgs) => args[5],
  ],
  (
    staticBlocks,
    textBlocks,
    thinkingBlocks,
    blockOrder,
    blockTypes,
    displayedTextLength,
  ): ContentBlock[] => {
    let remainingChars = displayedTextLength;
    const blocks: ContentBlock[] = [...staticBlocks];

    // 先输出 thinking blocks
    for (const blockId of blockOrder) {
      if (blockTypes[blockId] === 'thinking') {
        blocks.push({
          type: 'thinking' as const,
          id: blockId,
          thinking: thinkingBlocks[blockId] ?? '',
        });
      }
    }

    // 再输出 text blocks（带打字机截断）
    for (const blockId of blockOrder) {
      if (blockTypes[blockId] === 'text') {
        const fullText = textBlocks[blockId] ?? '';
        const visibleLength = Math.min(remainingChars, fullText.length);
        remainingChars -= visibleLength;
        blocks.push({
          type: 'text' as const,
          id: blockId,
          text: fullText.slice(0, visibleLength),
        });
      }
    }

    return blocks;
  },
);

export function selectStreamContentBlocks(state: StreamSlot): ContentBlock[] {
  return selectStreamContentBlocksFromParts(
    state.staticBlocks,
    state.textBlocks,
    state.thinkingBlocks,
    state.blockOrder,
    state.blockTypes,
    state.displayedTextLength,
  );
}

// 完整版 selector（不截断），用于流结束时写入最终消息
/** 全局流槽位同一时刻只装一条流；判断这条流现在是不是槽位的属主。
 *
 * 槽位空闲（messageId 为 null）时一律放行，保持与 endStream / initRun 归属判据一致：
 * 只拦"槽位已经属于别人"，不拦"还没人占"。
 *
 * 用 messageId 而不是 conversationId：messageId 在 startStream 后不再被任何 reducer
 * 改动，conversationId 还会因草稿会话转正被 migrateStreamConversation 迁移；且同一会话
 * 连续两轮也要能区分。
 *
 * 只用于流槽位状态（正文/思考 delta、run timeline、流状态与错误）。会话自身的状态——
 * 标题、推荐问题、会话列表刷新、全局错误——不受槽位归属影响，丢了槽位也要照常走完。
 */
export function ownsStreamSlot(state: StreamSlot, messageId: string | null): boolean {
  return state.messageId === null || state.messageId === messageId;
}

export function selectFullStreamContentBlocks(state: StreamSlot): ContentBlock[] {
  const blocks: ContentBlock[] = [...state.staticBlocks];

  for (const blockId of state.blockOrder) {
    const type = state.blockTypes[blockId];
    if (type === 'thinking') {
      blocks.push({
        type: 'thinking' as const,
        id: blockId,
        thinking: state.thinkingBlocks[blockId] ?? '',
      });
    }
  }

  for (const blockId of state.blockOrder) {
    if (state.blockTypes[blockId] === 'text') {
      blocks.push({
        type: 'text' as const,
        id: blockId,
        text: state.textBlocks[blockId] ?? '',
      });
    }
  }

  return blocks;
}

export const {
  advanceTypewriter,
  appendTextDelta,
  appendThinkingDelta,
  applyPlanSnapshot,
  clearStreamError,
  clearCurrentRun,
  completeThinkingPhase,
  discardContentBlock,
  endStream,
  finalizeRun,
  setRunStopConfirmation,
  finalizeStep,
  finalizeToolCall,
  initRun,
  markLimitReached,
  mergeToolCallDelta,
  migrateStreamConversation,
  resetStreamState,
  pushStep,
  pushToolCall,
  setLastEntryId,
  setStreamError,
  setStreamStatus,
  startStream,
  updatePlanStep,
  updateRunProgress,
  updateContextUsage,
  receiveContextRequired,
  receiveContextResult,
  setContextRequestPhase,
  upsertEvidenceItem,
  upsertStaticContentBlock,
  upsertToolDigest,
} = streamSlice.actions;

export default streamSlice.reducer;
