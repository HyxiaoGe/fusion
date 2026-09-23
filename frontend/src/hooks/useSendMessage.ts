import { useCallback, useEffect, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { useAppDispatch, useAppSelector } from '@/redux/hooks';
import { useStore } from 'react-redux';
// localStorage 标记已移除，完全依赖后端 stream-status 判断是否重连
import {
  applySuggestedQuestionsPending,
  applySuggestedQuestionsReady,
  appendMessage,
  materializeConversation,
  mergeHydratedConversation,
  replaceMessage,
  removeConversation,
  removeMessage,
  requestConversationListRefresh,
  requestSuggestedQuestionsObservation,
  setAnimatingTitleId,
  setGlobalError,
  setHydrationStatus,
  setPendingConversationId,
  updateConversationTitle,
  updateConversationKnowledgeBaseIds,
  updateMessage,
  upsertConversation,
} from '@/redux/slices/conversationSlice';
import { resolveComposerAgentMode } from '@/lib/agent/composerAgentMode';
import {
  appendTextDelta,
  appendThinkingDelta,
  clearCurrentRun,
  completeThinkingPhase,
  endStream,
  finalizeRun,
  setRunStopConfirmation,
  migrateStreamConversation,
  selectFullStreamContentBlocks,
  ownsStreamSlot,
  selectStreamContentBlocks,
  setStreamError,
  setStreamStatus,
  startStream,
} from '@/redux/slices/streamSlice';
import {
  getChatCapabilities,
  isRecoverableStreamError,
  reconnectStream,
  sendMessageStream,
} from '@/lib/api/chat';
import { verifyStoppedRun } from '@/lib/chat/stopVerification';
import { clearStopOutcomeNotice, saveStopOutcomeNotice } from '@/lib/chat/stopOutcomeNotice';
import type { AgentRunState, AgentRunStatus } from '@/types/agentRun';
import type { StreamCallbacks } from '@/lib/api/chat';
import { runResumableStream } from '@/lib/api/resumableStream';
import { createAgentStreamEventHandlers } from '@/lib/agent/streamEventHandlers';
import {
  recoverReasoningOnlyFinalBlocks,
  shouldRecoverReasoningOnlyFinalBlocks,
} from '@/lib/chat/contentBlocks';
import {
  getConversationDetailRequestMetadata,
  invalidateConversationDetail,
  isStaleConversationDetailRequestError,
  loadConversationDetail,
} from '@/lib/chat/conversationDetailResource';
import {
  getConversationHydrationMetadata,
  getProtectedHydrationMessageIds,
} from '@/lib/chat/conversationHydrationMerge';
import {
  getSendModelErrorMessage,
  isModelAvailableForSending,
  resolveSendModel,
} from '@/lib/chat/sendModelResolution';
import {
  clearFirstTurnContextState,
  moveFirstTurnContextState,
} from '@/lib/chat/contextStatusPersistence';
import { hasFormalTextContent } from '@/lib/chat/suggestedQuestionState';
import type { StreamState } from '@/redux/slices/streamSlice';
import { selectStreamSlot } from '@/redux/slices/streamSlice';
import {
  findStreamController,
  getStreamController,
  migrateStreamController,
  registerStreamController,
  releaseStreamController,
  updateStreamController,
  waitForStreamIdentity,
} from '@/lib/chat/streamControllerRegistry';
import type { Message, ContentBlock } from '@/types/conversation';
import type { FileAttachment } from '@/lib/utils/fileHelpers';
import { selectAuthSessionKey } from '@/redux/selectors';
import { useTypewriter } from './useTypewriter';
import { useRetryMessage } from './useRetryMessage';
import type { RootState } from '@/redux/store';

type SendMessageOptions = {
  conversationId: string | null;
  /** 重试在删除原消息前已经验证过的会话模型，避免删除后被误判成新对话。 */
  resolvedModelId?: string;
  /** 标记为新对话（即使提供了 conversationId，也当作草稿处理）。用于首页上传文件后发送的场景 */
  isDraft?: boolean;
  /** 本地草稿会话已创建，可用于先进入会话页，不必等待服务端 materialize */
  onDraftCreated?: (draftConversationId: string) => void;
  onMaterialized?: (serverConversationId: string) => void;
  onStreamEnd?: (conversationId: string) => void;
  /** undefined=保持，[]=清空，非空数组按服务端能力上限替换会话知识库选择。 */
  knowledgeBaseIds?: string[];
  /** 安全重试复用原轮次 user ID，禁止追加重复问题。 */
  retryUserMessageId?: string;
  /** 原轮次已有回答时复用 assistant ID，并由服务端原位替换。 */
  retryAssistantMessageId?: string;
  /** Agent run 重试必须指向用户在轨迹标签页选择的真实 run。 */
  previousRunId?: string;
  /** 异步准备期间持续复核轨迹动作仍指向可操作的最新运行。 */
  canStart?: () => boolean;
  /** 发送尚未被本地消息队列接收时通知输入区保留草稿。 */
  onRejectedBeforeSend?: () => void;
  /** 本地消息与流控制器均已建立，可以提交输入区清理或重试替换。 */
  onAccepted?: () => void;
};

const STOP_BEFORE_READY_RETRY_DELAYS_MS = [50, 150] as const;
const STOP_OPERATION_TIMEOUT_MS = 500;
const INTERRUPTED_HYDRATION_RETRY_MS = 300;
const activeSendPreparations = new Set<string>();

interface SendSessionContext {
  authSessionKey: string;
  conversationEpoch: number;
  generation: number;
}

interface ActiveRetryTurnSnapshot {
  conversationId: string;
  user: Message;
  assistant?: Message;
}

function captureSendSessionContext(
  state: RootState,
  generation: number
): SendSessionContext | null {
  const authSessionKey = selectAuthSessionKey(state);
  if (!authSessionKey) return null;
  return {
    authSessionKey,
    conversationEpoch: state.conversation.conversationListEpoch,
    generation,
  };
}

function isSendSessionCurrent(state: RootState, context: SendSessionContext): boolean {
  return (
    selectAuthSessionKey(state) === context.authSessionKey &&
    state.conversation.conversationListEpoch === context.conversationEpoch
  );
}

/** 停止后的水合只负责刷新被停的那一轮。其后已经出现的新消息必须留下。 */
function messageIdsAfterAnchor(
  state: RootState,
  conversationId: string,
  anchorIds: ReadonlySet<string>,
): string[] {
  const messages = state.conversation.byId[conversationId]?.messages ?? [];
  let anchorIndex = -1;
  messages.forEach((message, index) => {
    if (anchorIds.has(message.id)) anchorIndex = index;
  });
  if (anchorIndex < 0) return [];
  return messages.slice(anchorIndex + 1).map((message) => message.id);
}

function stopAbortError(): Error {
  const error = new Error('停止请求已超时');
  error.name = 'AbortError';
  return error;
}

async function waitForStopRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    throw stopAbortError();
  }
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, delayMs);
    const onAbort = () => {
      clearTimeout(timer);
      reject(stopAbortError());
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

const IMAGE_DIMENSION_ERROR_MESSAGE = '图片尺寸过小，当前模型要求宽高都大于 10 像素，请换一张更大的图片后重试';

function normalizeSendErrorMessage(message: string): string {
  if (
    message.includes('image length and width do not meet the model restrictions') ||
    message.includes('height:2 or width:2 must be larger than 10') ||
    (message.includes('InternalError.Algo.InvalidParameter') && message.includes('image'))
  ) {
    return IMAGE_DIMENSION_ERROR_MESSAGE;
  }

  return message;
}

function isInterruptedStreamSignal(value: unknown): boolean {
  const candidate = (
    typeof value === 'object' && value !== null
      ? value as { code?: unknown; message?: unknown }
      : {}
  );
  return (
    candidate.code === 'stream_interrupted' ||
    (
      candidate.code === 'stream_error' &&
      candidate.message === '用户中止'
    )
  );
}

// 标题不再由前端在终态后拉取：它只取决于首个提问，后端在 run 早期就生成好并
// 经 conversation_title_updated 事件推来。这里只保留会话列表刷新作为兜底
// （事件因封口或断线未送达时，刷新仍能拿到已落库的标题）。
function postStreamActions(
  conversationId: string,
  dispatch: ReturnType<typeof useAppDispatch>,
  isSessionCurrent: () => boolean,
) {
  if (!isSessionCurrent()) return;
  dispatch(requestConversationListRefresh(conversationId));
}

export function useSendMessage(activeConversationId?: string | null) {
  const dispatch = useAppDispatch();
  const store = useStore<RootState>();
  const reasoningEnabled = useAppSelector((state) => state.conversation.reasoningEnabled);
  const composerAgentMode = useAppSelector((state) => state.conversation.composerAgentMode);
  const authSessionKey = useAppSelector(selectAuthSessionKey);
  const conversationEpoch = useAppSelector(
    (state) => state.conversation.conversationListEpoch
  );
  const abortControllerRef = useRef<AbortController | null>(null);
  const stopInFlightPromiseRef = useRef<Promise<void> | null>(null);
  const activeConvIdRef = useRef<string | null>(null);
  const userMessageIdRef = useRef<string | null>(null);
  const assistantMessageIdRef = useRef<string | null>(null);
  // BE 在 run_started/onReady 给的真实 assistant message_id，stop 时校验用。
  // 不复用 assistantMessageIdRef（那是 placeholder，streaming 期渲染匹配仍要用）
  const serverMessageIdRef = useRef<string | null>(null);
  const serverTaskIdRef = useRef<string | null>(null);
  const assistantHasContentRef = useRef(false);
  const activeRetryTurnSnapshotRef = useRef<ActiveRetryTurnSnapshot | null>(null);
  const sendGenerationRef = useRef(0);
  const activeSendContextRef = useRef<SendSessionContext | null>(null);
  const typewriter = useTypewriter();
  const typewriterRef = useRef(typewriter);
  typewriterRef.current = typewriter;
  const sendBoundaryRef = useRef({ authSessionKey, conversationEpoch });

  const hydrateAuthoritativeConversation = useCallback(
    async (
      conversationId: string,
      isSessionCurrent: () => boolean,
      options?: { extraPreserveMessageIds?: (state: RootState) => string[] },
    ) => {
      if (!isSessionCurrent()) return;

      // SSE 完成后必须绕过发送前可能已挂起的详情请求，以“完成时”的本地消息
      // 作为合并基线重新取一次服务端快照。旧 API 若忽略客户端消息 ID，服务端
      // 快照会替换本地乐观副本；新 API 则会用同 ID 补齐 sequence / usage。
      invalidateConversationDetail(conversationId);
      const request = loadConversationDetail(conversationId, {
        requestMetadata: getConversationHydrationMetadata(store.getState(), conversationId),
      });
      const requestMetadata = getConversationDetailRequestMetadata(request);
      dispatch(setHydrationStatus({ id: conversationId, status: 'loading' }));

      try {
        const serverConversation = await request;
        if (!isSessionCurrent()) return;
        const state = store.getState();
        const preserveMessageIds = new Set(getProtectedHydrationMessageIds(
          state,
          conversationId,
          requestMetadata,
        ));
        for (const messageId of options?.extraPreserveMessageIds?.(state) ?? []) {
          preserveMessageIds.add(messageId);
        }
        dispatch(mergeHydratedConversation({
          conversation: serverConversation,
          preserveMessageIds: [...preserveMessageIds],
          requestMetadata,
        }));
      } catch (error) {
        if (isStaleConversationDetailRequestError(error) || !isSessionCurrent()) {
          return;
        }
        // 流式结果已经可用，权威快照刷新失败不应把正常完成的发送 UI 变成错误态。
        dispatch(setHydrationStatus({ id: conversationId, status: 'done' }));
      }
    },
    [dispatch, store]
  );

  const invalidateFrontendSend = useCallback(() => {
    sendGenerationRef.current += 1;
    const invalidatedConversationId = activeConvIdRef.current;
    const invalidatedController = abortControllerRef.current;
    if (invalidatedController) {
      releaseStreamController(activeConvIdRef.current, invalidatedController);
      invalidatedController.abort();
    }
    abortControllerRef.current = null;
    typewriterRef.current.stop();
    stopInFlightPromiseRef.current = null;
    activeConvIdRef.current = null;
    userMessageIdRef.current = null;
    assistantMessageIdRef.current = null;
    serverMessageIdRef.current = null;
    serverTaskIdRef.current = null;
    assistantHasContentRef.current = false;
    activeRetryTurnSnapshotRef.current = null;
    activeSendContextRef.current = null;
    if (invalidatedConversationId) {
      dispatch(endStream({ conversationId: invalidatedConversationId }));
    }
  }, [dispatch]);

  useEffect(() => {
    const previousBoundary = sendBoundaryRef.current;
    sendBoundaryRef.current = { authSessionKey, conversationEpoch };
    if (
      previousBoundary.authSessionKey !== authSessionKey ||
      previousBoundary.conversationEpoch !== conversationEpoch
    ) {
      invalidateFrontendSend();
    }
  }, [authSessionKey, conversationEpoch, invalidateFrontendSend]);

  useEffect(() => {
    return () => {
      const activeSendContext = activeSendContextRef.current;
      if (
        activeSendContext &&
        !isSendSessionCurrent(store.getState(), activeSendContext)
      ) {
        invalidateFrontendSend();
      }
    };
  }, [invalidateFrontendSend, store]);

  // 读取某个会话的流槽位。槽位按会话索引后，"当前流"不再是全局概念，
  // 每次读都必须说清楚读的是哪个会话。
  const getSlot = useCallback((conversationId: string | null | undefined) => (
    selectStreamSlot(store.getState() as { stream: StreamState }, conversationId)
  ), [store]);

  // 本次发送所属的会话。此前还会 fallback 到 Redux 里的全局 stream.conversationId
  // （恢复流写的），现在没有"全局那一条"了：要停哪个会话由调用方显式给出。
  const getStreamingConvId = useCallback((explicitConversationId?: string | null) => (
    activeConvIdRef.current ?? explicitConversationId ?? null
  ), []);

  // conversationId 由调用方给出要停哪个会话；不传时用本 hook 自己那条。
  const stopStreaming = useCallback((conversationId?: string | null): Promise<void> => {
    if (stopInFlightPromiseRef.current) {
      return stopInFlightPromiseRef.current;
    }

    const stopOperation = (async () => {
      const convId = getStreamingConvId(conversationId);
      const registeredStream = convId ? getStreamController(convId) : null;
      const userMsgId = userMessageIdRef.current;
      const assistantMsgId = assistantMessageIdRef.current;
      // 跨 hook/页面停止时本实例 ref 为空，必须在释放注册表前固定原运行身份。
      const serverMsgId = serverMessageIdRef.current || registeredStream?.messageId || null;
      const serverTaskId = serverTaskIdRef.current || registeredStream?.taskId || null;
      const retryTurnSnapshot = activeRetryTurnSnapshotRef.current;
      const stopSessionContext = activeSendContextRef.current
        ?? captureSendSessionContext(store.getState(), sendGenerationRef.current);
      const slotAtStop = convId ? getSlot(convId) : null;
      const stoppedRunId = slotAtStop?.currentRun?.status === 'running'
        ? slotAtStop.currentRun.runId
        : null;
      const stoppedSlotMessageId = slotAtStop?.messageId ?? null;
      const pendingConversationId = (
        store.getState() as { conversation: { pendingConversationId: string | null } }
      ).conversation.pendingConversationId;
      const shouldDiscardPendingDraft = Boolean(
        convId && pendingConversationId === convId
      );

      typewriterRef.current.stop();

      const stoppingController = abortControllerRef.current
        ?? registeredStream?.controller
        ?? null;

      // 无身份时先等原发送写出 task_id。此时不能 abort SSE，也不能抬升
      // generation：那会毁掉唯一能拿到身份的通道，随后只能发出会话级宽停止。
      let remoteTaskId = serverTaskId;
      let remoteMsgId = serverMsgId;
      let remoteConvId = convId;
      const stopController = new AbortController();
      const stopTimeout = setTimeout(
        () => stopController.abort(),
        STOP_OPERATION_TIMEOUT_MS,
      );
      if (!remoteTaskId && stoppingController) {
        try {
          const identity = await waitForStreamIdentity(
            stoppingController,
            stopController.signal,
          );
          if (identity?.taskId) {
            remoteTaskId = identity.taskId;
            remoteMsgId = identity.messageId || remoteMsgId;
            remoteConvId = identity.conversationId || remoteConvId;
          }
        } catch (error) {
          if (!stopController.signal.aborted) {
            console.warn('等待停止身份失败，已改为本地停止', error);
          }
        }
      }
      const teardownConvId = (
        stoppingController
          ? findStreamController(stoppingController)?.conversationId
          : null
      ) ?? remoteConvId ?? convId;
      let effectiveStoppedRunId = stoppedRunId;
      if (!effectiveStoppedRunId && teardownConvId) {
        const slotNow = getSlot(teardownConvId);
        const stillOriginal = !slotNow.messageId
          || slotNow.messageId === assistantMsgId
          || slotNow.messageId === stoppedSlotMessageId
          || slotNow.messageId === remoteMsgId;
        if (stillOriginal && slotNow.currentRun?.status === 'running') {
          effectiveStoppedRunId = slotNow.currentRun.runId;
        }
      }

      const stopRequestedAt = Date.now();
      const stoppedRunSnapshot = teardownConvId ? getSlot(teardownConvId).currentRun : null;
      const originalMessageId = assistantMsgId ?? stoppedSlotMessageId ?? remoteMsgId;
      const isStopSessionCurrent = () => Boolean(stopSessionContext
        && isSendSessionCurrent(store.getState(), stopSessionContext));
      const writeStoppedMessageRun = (run: AgentRunState) => {
        if (!teardownConvId || !originalMessageId || !isStopSessionCurrent()) return;
        const slot = getSlot(teardownConvId);
        if (slot.isStreaming && slot.messageId === originalMessageId && slot.currentRun?.runId !== run.runId) return;
        const message = store.getState().conversation.byId[teardownConvId]?.messages.find(item => item.id === originalMessageId);
        if (!message || (message.agent_run && message.agent_run.runId !== run.runId)) return;
        if (run.status === 'running' && (
          (message.agent_run && message.agent_run.status !== 'running')
          || (slot.currentRun?.runId === run.runId && slot.currentRun.status !== 'running')
        )) return;
        dispatch(updateMessage({conversationId: teardownConvId, messageId: originalMessageId, patch: {agent_run: run}}));
      };
      const markStopConfirmation = (status: 'pending' | 'unconfirmed') => {
        if (!teardownConvId || !effectiveStoppedRunId || retryTurnSnapshot || !isStopSessionCurrent()) return;
        const confirmation = { status, requestedAt: stopRequestedAt };
        if (status === 'unconfirmed' && stopSessionContext) {
          saveStopOutcomeNotice({
            authIdentity: stopSessionContext.authSessionKey,
            conversationId: teardownConvId,
            runId: effectiveStoppedRunId,
            messageId: remoteMsgId ?? null,
            requestedAt: stopRequestedAt,
            terminalStatus: null,
          });
        }
        dispatch(setRunStopConfirmation({conversationId: teardownConvId, runId: effectiveStoppedRunId, confirmation}));
        if (stoppedRunSnapshot?.runId === effectiveStoppedRunId && stoppedRunSnapshot.status === 'running') {
          writeStoppedMessageRun({...stoppedRunSnapshot, stopConfirmation: confirmation});
        }
      };
      markStopConfirmation('pending');

      if (stoppingController) {
        releaseStreamController(teardownConvId, stoppingController);
        releaseStreamController(convId, stoppingController);
        stoppingController.abort();
      }
      if (abortControllerRef.current === stoppingController) {
        abortControllerRef.current = null;
      }

      if (teardownConvId && userMsgId) {
        if (
          retryTurnSnapshot?.conversationId === convId
          && retryTurnSnapshot.user.id === userMsgId
        ) {
          dispatch(replaceMessage({
            conversationId: convId,
            messageId: userMsgId,
            message: retryTurnSnapshot.user,
          }));
        } else {
          dispatch(updateMessage({
            conversationId: convId,
            messageId: userMsgId,
            patch: { status: null },
          }));
        }
      }

      // 把 streamSlice 已有内容写回 assistant 消息，防止 endStream 清空后丢失
      if (convId && assistantMsgId) {
        const retryAssistant = retryTurnSnapshot?.conversationId === convId
          && retryTurnSnapshot.assistant?.id === assistantMsgId
          ? retryTurnSnapshot.assistant
          : undefined;
        if (retryAssistant) {
          dispatch(replaceMessage({
            conversationId: convId,
            messageId: assistantMsgId,
            message: retryAssistant,
          }));
        } else if (retryTurnSnapshot?.conversationId === convId) {
          dispatch(removeMessage({
            conversationId: convId,
            messageId: assistantMsgId,
          }));
        } else {
          const partialBlocks = selectFullStreamContentBlocks(getSlot(convId));
          if (partialBlocks.length > 0) {
            dispatch(updateMessage({
              conversationId: convId,
              messageId: assistantMsgId,
              patch: { content: partialBlocks },
            }));
          }
        }
      } else if (convId && stoppedSlotMessageId && !retryTurnSnapshot) {
        const partialBlocks = selectFullStreamContentBlocks(getSlot(convId));
        if (partialBlocks.length > 0) {
          dispatch(updateMessage({
            conversationId: convId,
            messageId: stoppedSlotMessageId,
            patch: { content: partialBlocks },
          }));
        }
      }

      if (shouldDiscardPendingDraft && convId) {
        dispatch(removeConversation(convId));
        dispatch(setPendingConversationId(null));
      }

      if (convId) clearFirstTurnContextState(convId);
      if (convId && retryTurnSnapshot?.assistant) {
        dispatch(clearCurrentRun({ conversationId: convId }));
      }
      if (teardownConvId) {
        dispatch(endStream({
          conversationId: teardownConvId,
          messageId: assistantMsgId ?? stoppedSlotMessageId,
        }));
      }
      sendGenerationRef.current += 1;
      activeSendContextRef.current = null;
      activeConvIdRef.current = null;
      userMessageIdRef.current = null;
      assistantMessageIdRef.current = null;
      serverMessageIdRef.current = null;
      serverTaskIdRef.current = null;
      assistantHasContentRef.current = false;
      activeRetryTurnSnapshotRef.current = null;

      // 远端只按已解析的原 task_id 取消。没有身份就不发请求：
      // 在途的会话级停止会在下一轮启动后误杀新任务。
      let stopConfirmed = false;
      try {
        if (remoteConvId && remoteTaskId) {
          const { stopStream } = await import('@/lib/api/chat');
          const requestRemoteStop = () => stopStream(
            remoteConvId,
            remoteMsgId || undefined,
            stopController.signal,
            undefined,
            remoteTaskId,
          );
          let cancelled = await requestRemoteStop();
          if (!cancelled) {
            for (const delayMs of STOP_BEFORE_READY_RETRY_DELAYS_MS) {
              if (cancelled || stopController.signal.aborted) break;
              await waitForStopRetry(delayMs, stopController.signal);
              cancelled = await requestRemoteStop();
            }
          }
          stopConfirmed = cancelled === true;
        }
      } catch (error) {
        if (!stopController.signal.aborted) {
          console.warn('停止后台生成失败，已完成本地停止', error);
        }
      } finally {
        clearTimeout(stopTimeout);
      }

      // HTTP 超时只表示未收到确认。独立查询原 run，绝不把会话的最新运行
      // 或另一个 message 的终态当作这次停止结果；整个核实过程有独立上限。
      let terminalStatus: Exclude<AgentRunStatus, 'running'> | null = stopConfirmed ? 'interrupted' : null;
      if (!terminalStatus && remoteConvId && effectiveStoppedRunId && isStopSessionCurrent()) {
        terminalStatus = await verifyStoppedRun(remoteConvId, effectiveStoppedRunId, remoteMsgId, isStopSessionCurrent);
      }
      if (!isStopSessionCurrent()) return;
      if (!terminalStatus && teardownConvId && effectiveStoppedRunId) {
        const knownRun = store.getState().conversation.byId[teardownConvId]?.messages.find(message =>
          (message.id === originalMessageId || message.id === remoteMsgId)
          && message.agent_run?.runId === effectiveStoppedRunId
          && message.agent_run.status !== 'running',
        )?.agent_run;
        if (knownRun && knownRun.status !== 'running') terminalStatus = knownRun.status;
      }
      if (!terminalStatus) {
        markStopConfirmation('unconfirmed');
        if (!effectiveStoppedRunId && teardownConvId) {
          const slot = getSlot(teardownConvId);
          if (!slot.isStreaming && (!slot.messageId || slot.messageId === originalMessageId)) {
            dispatch(setStreamError({conversationId: teardownConvId, code: 'stop_unconfirmed', message: '已停止接收回答，但未确认后台是否停止。请刷新会话核实。'}));
          }
        }
      } else if (teardownConvId && effectiveStoppedRunId) {
        clearStopOutcomeNotice(teardownConvId, effectiveStoppedRunId);
      }

      const stateConvId = teardownConvId ?? convId;
      const hydrationAnchorIds = new Set(
        [userMsgId, assistantMsgId, serverMsgId, remoteMsgId, stoppedSlotMessageId].filter(
          (messageId): messageId is string => Boolean(messageId),
        ),
      );
      let interruptedFallback: ReturnType<typeof getSlot>['currentRun'] = null;
      const slotAfterStop = stateConvId ? getSlot(stateConvId) : null;
      const runAfterStop = slotAfterStop?.currentRun ?? null;
      const slotStillOwnsStoppedRun = !slotAfterStop?.messageId
        || slotAfterStop.messageId === assistantMsgId
        || slotAfterStop.messageId === stoppedSlotMessageId
        || slotAfterStop.messageId === remoteMsgId;
      if (
        terminalStatus
        && stateConvId
        && effectiveStoppedRunId
        && !retryTurnSnapshot
        && runAfterStop?.runId === effectiveStoppedRunId
        && runAfterStop.status === 'running'
        && slotStillOwnsStoppedRun
      ) {
        dispatch(finalizeRun({
          conversationId: stateConvId,
          runId: effectiveStoppedRunId,
          status: terminalStatus,
          reason: terminalStatus === 'interrupted' ? 'user_cancelled' : undefined,
          sequence: runAfterStop.lastSequence + 1,
        }));
        const finalized = getSlot(stateConvId).currentRun;
        if (finalized?.runId === effectiveStoppedRunId && finalized.status === terminalStatus) {
          interruptedFallback = finalized;
          const localMessageId = assistantMsgId ?? stoppedSlotMessageId ?? remoteMsgId;
          if (localMessageId) {
            dispatch(updateMessage({
              conversationId: stateConvId,
              messageId: localMessageId,
              patch: { agent_run: finalized },
            }));
          }
        }
      }

      if (terminalStatus && !interruptedFallback && !retryTurnSnapshot && stoppedRunSnapshot?.runId === effectiveStoppedRunId) {
        const finished: AgentRunState = {...stoppedRunSnapshot, status: terminalStatus};
        delete finished.stopConfirmation;
        interruptedFallback = finished;
        writeStoppedMessageRun(finished);
      }

      const reapplyInterruptedIfSnapshotStillRunning = () => {
        if (!stateConvId || !effectiveStoppedRunId || !interruptedFallback) return false;
        const messages = store.getState().conversation.byId[stateConvId]?.messages ?? [];
        let stillRunning = false;
        for (const message of messages) {
          const agentRun = message.agent_run;
          if (!agentRun || agentRun.runId !== effectiveStoppedRunId || agentRun.status !== 'running') continue;
          const slot = getSlot(stateConvId);
          if (
            slot.isStreaming
            && slot.messageId === message.id
            && slot.currentRun?.runId !== effectiveStoppedRunId
          ) {
            continue;
          }
          stillRunning = true;
          dispatch(updateMessage({
            conversationId: stateConvId,
            messageId: message.id,
            patch: {
              agent_run: {
                ...interruptedFallback,
                messageId: message.id,
                serverMessageId: message.id,
                status: interruptedFallback.status,
              },
            },
          }));
        }
        return stillRunning;
      };

      if (stateConvId && stopSessionContext && (retryTurnSnapshot?.user || interruptedFallback)) {
        const sessionCurrent = () => isSendSessionCurrent(store.getState(), stopSessionContext);
        const preserveLaterMessages = (state: RootState) => (
          messageIdsAfterAnchor(state, stateConvId, hydrationAnchorIds)
        );
        await hydrateAuthoritativeConversation(stateConvId, sessionCurrent, {
          extraPreserveMessageIds: preserveLaterMessages,
        });
        // /stop 先完成取消，Agent run 落成 interrupted 可能晚一拍。
        // 第一次详情若仍是 running，不能把它写回页面；只再取一次权威快照。
        if (reapplyInterruptedIfSnapshotStillRunning()) {
          setTimeout(() => {
            if (!sessionCurrent()) return;
            void hydrateAuthoritativeConversation(stateConvId, sessionCurrent, {
              extraPreserveMessageIds: preserveLaterMessages,
            }).then(() => {
              reapplyInterruptedIfSnapshotStillRunning();
            });
          }, INTERRUPTED_HYDRATION_RETRY_MS);
        }
      }
    })();

    stopInFlightPromiseRef.current = stopOperation;
    void stopOperation.then(
      () => {
        if (stopInFlightPromiseRef.current === stopOperation) {
          stopInFlightPromiseRef.current = null;
        }
      },
      () => {
        if (stopInFlightPromiseRef.current === stopOperation) {
          stopInFlightPromiseRef.current = null;
        }
      }
    );
    return stopOperation;
  }, [dispatch, store, getSlot, getStreamingConvId, hydrateAuthoritativeConversation]);

  const sendMessage = useCallback(
    async (content: string, options: SendMessageOptions, attachments?: FileAttachment[]) => {
      if (options.canStart && !options.canStart()) {
        options.onRejectedBeforeSend?.();
        return;
      }
      if (!content.trim() && (!attachments || attachments.length === 0)) return;
      const preparationContext = captureSendSessionContext(
        store.getState(),
        sendGenerationRef.current,
      );
      if (!preparationContext) {
        options.onRejectedBeforeSend?.();
        return;
      }
      const sendSessionKey = preparationContext.authSessionKey;
      // 这里此前有一道发送互斥：全局只有一个流槽位，别的会话在跑时必须拒绝，
      // 否则新流会把它的状态覆盖掉；拒绝还要弹 toast 说明为什么回车没反应。
      // 槽位按会话拆开后，"别的会话占着我的槽位"这个条件不再存在——各写各的，
      // 互斥连同那条提示一起去掉。同一会话连按仍由下面的 activeSendPreparations 挡。
      // 连续触发同一次发送（回车连按）不提示，那是重复提交而不是被别的流挡住。
      if (activeSendPreparations.has(sendSessionKey)) {
        options.onRejectedBeforeSend?.();
        return;
      }
      activeSendPreparations.add(sendSessionKey);
      let preparationReleased = false;
      const releaseSendPreparation = () => {
        if (preparationReleased) return;
        preparationReleased = true;
        activeSendPreparations.delete(sendSessionKey);
      };
      const rejectStalePreparation = () => {
        if (
          isSendSessionCurrent(store.getState(), preparationContext)
          && (!options.canStart || options.canStart())
        ) return false;
        releaseSendPreparation();
        options.onRejectedBeforeSend?.();
        return true;
      };

      try {
        if (rejectStalePreparation()) return;
        if (stopInFlightPromiseRef.current) {
          await stopInFlightPromiseRef.current;
          if (rejectStalePreparation()) return;
        }

        if (abortControllerRef.current) {
          await stopStreaming();
          if (rejectStalePreparation()) return;
        }
      } catch (error) {
        releaseSendPreparation();
        throw error;
      }

      const isDraft = options.isDraft ?? (options.conversationId === null);
      const currentState = store.getState();
      const useResolvedModel = Boolean(
        options.resolvedModelId
        && !isDraft
        && options.conversationId,
      );
      const prevalidatedModel = useResolvedModel
        ? currentState.models.models.find((model) => (
            model.id === options.resolvedModelId
            && isModelAvailableForSending(model)
          ))
        : null;
      const modelResolution = prevalidatedModel
        ? { status: 'ready' as const, model: prevalidatedModel }
        : useResolvedModel
          ? { status: 'conversation_model_unavailable' as const }
          : resolveSendModel(
              currentState,
              isDraft ? null : options.conversationId,
            );
      if (modelResolution.status !== 'ready') {
        dispatch(setGlobalError(getSendModelErrorMessage(modelResolution)));
        releaseSendPreparation();
        options.onRejectedBeforeSend?.();
        return;
      }
      const enabledModel = modelResolution.model;
      const effectiveKnowledgeBaseIds = options.knowledgeBaseIds ?? (
        !isDraft && options.conversationId
          ? currentState.conversation.byId[options.conversationId]?.knowledge_base_ids
          : undefined
      );
      const strictKnowledgeMode = Boolean(effectiveKnowledgeBaseIds?.length);
      if (strictKnowledgeMode) {
        let capabilities: Awaited<ReturnType<typeof getChatCapabilities>>;
        try {
          capabilities = await getChatCapabilities();
        } catch {
          dispatch(setGlobalError('知识库问答当前不可用，请刷新页面后重试'));
          releaseSendPreparation();
          options.onRejectedBeforeSend?.();
          return;
        }
        if (rejectStalePreparation()) return;
        const maxKnowledgeBases = capabilities.knowledge_grounding_max_bases;
        if (
          !capabilities.knowledge_grounding_v1
          || !Number.isSafeInteger(maxKnowledgeBases)
          || maxKnowledgeBases < 1
        ) {
          dispatch(setGlobalError('知识库问答当前不可用，请刷新页面后重试'));
          releaseSendPreparation();
          options.onRejectedBeforeSend?.();
          return;
        }
        if ((effectiveKnowledgeBaseIds?.length ?? 0) > maxKnowledgeBases) {
          dispatch(setGlobalError(`最多只能选择 ${maxKnowledgeBases} 个知识库`));
          releaseSendPreparation();
          options.onRejectedBeforeSend?.();
          return;
        }
      }
      const agentModeResolution = resolveComposerAgentMode(
        strictKnowledgeMode ? 'auto' : composerAgentMode,
        enabledModel.capabilities,
      );

      if (rejectStalePreparation()) return;
      const nextGeneration = sendGenerationRef.current + 1;
      const sendContext = captureSendSessionContext(store.getState(), nextGeneration);
      if (!sendContext) {
        releaseSendPreparation();
        options.onRejectedBeforeSend?.();
        return;
      }
      sendGenerationRef.current = nextGeneration;
      activeSendContextRef.current = sendContext;
      const isSessionCurrent = () => isSendSessionCurrent(store.getState(), sendContext);
      const isActiveSendCurrent = () => (
        sendGenerationRef.current === sendContext.generation &&
        isSessionCurrent()
      );

      // 只用于流槽位状态。丢了槽位仍要写完本会话自身的标题、推荐问题与列表刷新，
      // 所以不能把它并进 isActiveSendCurrent。
      // 跨会话串槽已由按会话拆分的槽位结构消灭；这里剩下的是同一会话内
      // 上一轮的迟到回调——messageId 仍是唯一能区分两轮的身份。
      const ownsSlot = () => ownsStreamSlot(
        getSlot(activeConvIdRef.current),
        assistantMessageIdRef.current,
      );

      const tempConvId = isDraft && !options.conversationId ? uuidv4() : options.conversationId!;
      const retryConversation = options.retryUserMessageId
        ? currentState.conversation.byId[tempConvId]
        : undefined;
      const retryTurnSnapshot = options.retryUserMessageId
        ? {
            user: retryConversation?.messages.find(
              (message) => message.id === options.retryUserMessageId,
            ),
            assistant: options.retryAssistantMessageId
              ? retryConversation?.messages.find(
                  (message) => message.id === options.retryAssistantMessageId,
                )
              : undefined,
          }
        : null;
      activeRetryTurnSnapshotRef.current = retryTurnSnapshot?.user
        ? {
            conversationId: tempConvId,
            user: retryTurnSnapshot.user,
            ...(retryTurnSnapshot.assistant ? { assistant: retryTurnSnapshot.assistant } : {}),
          }
        : null;
      const previousKnowledgeSelection = (
        !isDraft
        && options.knowledgeBaseIds !== undefined
        && currentState.conversation.byId[tempConvId]
      )
        ? {
            knowledgeBaseIds: currentState.conversation.byId[tempConvId].knowledge_base_ids,
            updatedAt: currentState.conversation.byId[tempConvId].updatedAt,
          }
        : null;

      // 发送开始后，发送前发出的详情 GET 已不再能代表当前会话。立即让它失效，
      // 并结束其 loading 状态，避免迟到快照覆盖本轮乐观消息或永久停在加载态。
      invalidateConversationDetail(tempConvId);
      if (options.conversationId) {
        dispatch(setHydrationStatus({ id: tempConvId, status: 'done' }));
      }

      if (isDraft) {
        dispatch(setPendingConversationId(tempConvId));
        dispatch(
          upsertConversation({
            id: tempConvId,
            title: content.substring(0, 30),
            model_id: enabledModel.id,
            knowledge_base_ids: options.knowledgeBaseIds ?? [],
            messages: [],
            createdAt: Date.now(),
            updatedAt: Date.now(),
          })
        );
      }

      if (!isDraft && options.knowledgeBaseIds !== undefined) {
        dispatch(updateConversationKnowledgeBaseIds({
          id: tempConvId,
          knowledgeBaseIds: options.knowledgeBaseIds,
        }));
      }

      activeConvIdRef.current = tempConvId;
      assistantHasContentRef.current = false;

      const isMessageRetry = Boolean(options.retryUserMessageId);
      const userMessageId = options.retryUserMessageId ?? uuidv4();
      const assistantMessageId = options.retryAssistantMessageId ?? uuidv4();
      userMessageIdRef.current = userMessageId;
      assistantMessageIdRef.current = assistantMessageId;
      // 清理上一轮残留的 server message id，等本轮 onReady 重新写入
      serverMessageIdRef.current = null;
      serverTaskIdRef.current = null;

      // 构建用户消息 content blocks（文本 + 文件）
      const contentBlocks: ContentBlock[] = [
        { type: 'text', id: `blk_${userMessageId.slice(0, 12)}`, text: content.trim() },
      ];
      if (attachments) {
        for (const att of attachments) {
          contentBlocks.push({
            type: 'file',
            id: `blk_${uuidv4().slice(0, 12)}`,
            file_id: att.fileId,
            filename: att.filename,
            mime_type: att.mimeType,
            // 图片文件用本地 previewUrl 作为即时缩略图，后端持久化的 thumbnail_url 在刷新后生效
            thumbnail_url: att.mimeType.startsWith('image/') ? att.previewUrl : undefined,
          });
        }
      }

      const fileIds = attachments?.map((a) => a.fileId);

      const userMessage: Message = {
        id: userMessageId,
        role: 'user',
        content: contentBlocks,
        status: 'pending',
        timestamp: Date.now(),
      };

      const assistantPlaceholder: Message = {
        id: assistantMessageId,
        role: 'assistant',
        content: [],
        timestamp: Date.now(),
      };

      if (isMessageRetry) {
        dispatch(updateMessage({
          conversationId: tempConvId,
          messageId: userMessageId,
          patch: { status: 'pending' },
        }));
        if (options.retryAssistantMessageId) {
          dispatch(updateMessage({
            conversationId: tempConvId,
            messageId: assistantMessageId,
            patch: {
              content: [],
              model_id: enabledModel.id,
              usage: null,
              agent_run: null,
              suggestedQuestions: undefined,
              suggestedQuestionsStatus: 'idle',
              timestamp: Date.now(),
            },
          }));
        } else {
          dispatch(appendMessage({ conversationId: tempConvId, message: assistantPlaceholder }));
        }
      } else {
        dispatch(appendMessage({ conversationId: tempConvId, message: userMessage }));
        dispatch(appendMessage({ conversationId: tempConvId, message: assistantPlaceholder }));
      }
      dispatch(startStream({ conversationId: tempConvId, messageId: assistantMessageId }));

      const controller = new AbortController();
      abortControllerRef.current = controller;
      // 同步登记到按会话索引的注册表：停止时按会话查表，而不是按「哪个 ref 非空」猜。
      registerStreamController({
        conversationId: tempConvId,
        kind: 'send',
        controller,
      });
      // 注销一律带上 controller，迟到的收尾便不会注销后来者。
      const releaseSendController = () => {
        releaseStreamController(activeConvIdRef.current, controller);
      };
      releaseSendPreparation();
      options.onAccepted?.();
      if (isDraft && isActiveSendCurrent()) {
        options.onDraftCreated?.(tempConvId);
      }
      const supportsReasoning = enabledModel.capabilities?.deepThinking ?? false;
      const useReasoning = reasoningEnabled && supportsReasoning;
      let serverConvId: string | null = null;
      let materializedOnce = false;
      let postStreamActionsStarted = false;
      // usage 不再随 done 事件下发（spec 缺口，未来可能扩 RunCompleted.usage）；
      // 当前路径：agent 模式从 GET conversation 拉，普通模式暂留 undefined
      let donePayload: { incomingConvId: string } | null = null;

      const materializeIfNeeded = (incomingConvId?: string) => {
        if (
          !isActiveSendCurrent() ||
          !isDraft ||
          !incomingConvId ||
          materializedOnce
        ) return;

        materializedOnce = true;
        serverConvId = incomingConvId;
        // 键必须跟着会话 ID 一起迁移，否则这条流在新 ID 下查不到、旧 ID 下永不注销。
        migrateStreamController(activeConvIdRef.current ?? tempConvId, incomingConvId, controller);
        activeConvIdRef.current = incomingConvId;
        // 首页会在物化后重挂输入区，必须先把首轮上下文偏好迁移到服务端会话 ID。
        moveFirstTurnContextState(tempConvId, incomingConvId);
        // 迁移流标记：tempConvId → serverConvId
        dispatch(
          materializeConversation({
            pendingId: tempConvId,
            serverConversation: {
              id: incomingConvId,
              title: content.substring(0, 30),
              model_id: enabledModel.id,
              knowledge_base_ids: options.knowledgeBaseIds ?? [],
              messages: [
                { ...userMessage, chatId: incomingConvId },
                { ...assistantPlaceholder, chatId: incomingConvId },
              ],
              createdAt: Date.now(),
              updatedAt: Date.now(),
            },
          })
        );
        dispatch(migrateStreamConversation({ from: tempConvId, to: incomingConvId }));
        options.onMaterialized?.(incomingConvId);
      };

      // pending 与 ready 必须落在同一条 assistant 消息上：草稿期本地 ID 与
      // 服务端 ID 可能尚未合流，两者都要能对上才允许写入。
      const resolveSuggestedQuestionsTarget = (eventMessageId: string) => {
        if (!isActiveSendCurrent() || !activeConvIdRef.current) return null;
        const localMessageId = assistantMessageIdRef.current;
        const knownServerMessageId = serverMessageIdRef.current;
        const activeConversation = (
          store.getState() as RootState
        ).conversation.byId[activeConvIdRef.current];
        const hasDirectMessage = activeConversation?.messages.some(
          message => message.id === eventMessageId && message.role === 'assistant',
        );
        if (
          !hasDirectMessage
          && (!localMessageId || knownServerMessageId !== eventMessageId)
        ) {
          return null;
        }
        return {
          conversationId: activeConvIdRef.current,
          messageId: eventMessageId,
          localMessageId: (knownServerMessageId === eventMessageId
            ? localMessageId
            : undefined) ?? undefined,
        };
      };

      const startPostStreamActions = (conversationId: string) => {
        if (!isDraft || postStreamActionsStarted || !isSessionCurrent()) return;
        postStreamActionsStarted = true;
        postStreamActions(conversationId, dispatch, isSessionCurrent);
      };

      const doCompleteStream = (payload: NonNullable<typeof donePayload>) => {
        if (!isActiveSendCurrent()) return;
        const { incomingConvId } = payload;
        materializeIfNeeded(incomingConvId);
        if (!isActiveSendCurrent()) return;

        const effectiveConvId = activeConvIdRef.current;
        if (!effectiveConvId) return;
        const finalConvId = serverConvId ?? incomingConvId ?? effectiveConvId;

        // 从 streamSlice 组装最终 content blocks
        const streamState = getSlot(finalConvId);
        const rawFinalBlocks = selectFullStreamContentBlocks(streamState);
        const finalBlocks = shouldRecoverReasoningOnlyFinalBlocks({
          runStatus: streamState.currentRun?.status,
          messageMatches: streamState.currentRun?.messageId === assistantMessageId,
        })
          ? recoverReasoningOnlyFinalBlocks(rawFinalBlocks)
          : rawFinalBlocks;
        const hasThinking = finalBlocks.some(b => b.type === 'thinking');

        // 槽位已经属于别人时，这里读到的是别人的正文，写下去就是把 B 的回答塞进 A 的消息。
        // 本轮内容在对方 startStream 时已被清掉，没有正确值可写，只能不覆盖：
        // 服务端那份仍然正确，随后的会话快照刷新会补上。
        const ownsSlotOnComplete = ownsSlot();
        dispatch(
          updateMessage({
            conversationId: finalConvId,
            messageId: assistantMessageId,
            patch: {
              ...(ownsSlotOnComplete ? { content: finalBlocks } : {}),
              model_id: enabledModel.id,
              timestamp: Date.now(),
              // usage：当前 done 事件不再携带；agent 模式由后续 GET conversation 拉取覆盖
              isReasoningVisible: hasThinking && ownsSlotOnComplete ? false : undefined,
            },
          })
        );
        if (ownsSlotOnComplete && hasFormalTextContent(rawFinalBlocks)) {
          dispatch(requestSuggestedQuestionsObservation({
            conversationId: finalConvId,
            messageIds: [
              assistantMessageId,
              serverMessageIdRef.current,
            ].filter((messageId): messageId is string => Boolean(messageId)),
          }));
        }
        dispatch(
          updateMessage({
            conversationId: finalConvId,
            messageId: userMessageId,
            patch: { status: null },
          })
        );
        dispatch(endStream({
          conversationId: finalConvId,
          messageId: assistantMessageIdRef.current,
        }));
        sendGenerationRef.current += 1;
        activeSendContextRef.current = null;
        releaseSendController();
        abortControllerRef.current = null;
        activeConvIdRef.current = null;
        userMessageIdRef.current = null;
        assistantMessageIdRef.current = null;
        serverMessageIdRef.current = null;
        serverTaskIdRef.current = null;
        assistantHasContentRef.current = false;
        activeRetryTurnSnapshotRef.current = null;
        options.onStreamEnd?.(finalConvId);
        void hydrateAuthoritativeConversation(finalConvId, isSessionCurrent);
        if (isDraft) {
          startPostStreamActions(finalConvId);
        } else {
          if (isSessionCurrent()) {
            dispatch(requestConversationListRefresh(finalConvId));
          }
        }
      };

      const rememberRemoteStopIdentity = (
        messageId?: string | null,
        taskId?: string | null,
      ) => {
        if (!isActiveSendCurrent()) return;
        if (messageId) serverMessageIdRef.current = messageId;
        if (taskId) serverTaskIdRef.current = taskId;
        const conversationId = activeConvIdRef.current;
        if (!conversationId) return;
        updateStreamController(conversationId, controller, {
          ...(messageId ? { messageId } : {}),
          ...(taskId ? { taskId } : {}),
        });
      };

      const streamCallbacks: StreamCallbacks = {
            onReady: ({
              messageId: incomingMessageId,
              conversationId: incomingConvId,
              taskId: incomingTaskId,
            }) => {
              if (!isActiveSendCurrent()) return;
              // 记录 BE 真实 message_id 供 stop 用（不污染 assistantMessageIdRef，
              // streaming 期渲染匹配仍然依赖本地 placeholder）
              serverMessageIdRef.current = incomingMessageId;
              serverTaskIdRef.current = incomingTaskId ?? null;
              materializeIfNeeded(incomingConvId);
              rememberRemoteStopIdentity(incomingMessageId, incomingTaskId ?? null);
            },

            onAnswering: (payload) => {
              if (!isActiveSendCurrent() || !activeConvIdRef.current || !ownsSlot()) return;
              // 收到第一个 text delta 且还在推理阶段 → 标记推理结束
              const streamConvId = activeConvIdRef.current;
              if (getSlot(streamConvId).isStreamingReasoning) {
                dispatch(completeThinkingPhase({ conversationId: streamConvId }));
              }
              assistantHasContentRef.current = true;
              dispatch(appendTextDelta({
                conversationId: streamConvId,
                blockId: payload.block_id,
                delta: payload.delta,
                runId: payload.run_id,
                stepId: payload.step_id,
              }));
              if (streamConvId) {
                typewriterRef.current.start(streamConvId, () => {
                  if (donePayload && isActiveSendCurrent()) doCompleteStream(donePayload);
                });
              }
            },

            onReasoning: (payload) => {
              if (!isActiveSendCurrent() || !activeConvIdRef.current || !ownsSlot()) return;
              dispatch(appendThinkingDelta({
                conversationId: activeConvIdRef.current,
                blockId: payload.block_id,
                delta: payload.delta,
                runId: payload.run_id,
                stepId: payload.step_id,
              }));
            },

            ...createAgentStreamEventHandlers({
              dispatch,
              trajectoryDispatch: dispatch,
              isActive: () => Boolean(activeConvIdRef.current) && isActiveSendCurrent() && ownsSlot(),
              // 优先本地 placeholder（streaming 期 message.id 是它），ref 为 null 时兜底用后端 ID。
              resolveMessageId: ev => assistantMessageIdRef.current ?? ev.message_id,
              setServerMessageId: messageId => {
                rememberRemoteStopIdentity(messageId);
              },
              resolveConversationId: () => activeConvIdRef.current,
              resolveTrajectoryConversationId: event => {
                const eventConversationId = event.eventType === 'run_started'
                  && typeof event.payload.conversation_id === 'string'
                  ? event.payload.conversation_id
                  : null;
                return eventConversationId || serverConvId || tempConvId;
              },
            }),

            onConversationTitleUpdated: ev => {
              if (!isSessionCurrent()) return;
              dispatch(updateConversationTitle({
                id: ev.conversation_id,
                title: ev.title,
              }));
              dispatch(setAnimatingTitleId(ev.conversation_id));
              setTimeout(() => {
                if (isSessionCurrent()) {
                  dispatch(setAnimatingTitleId(null));
                }
              }, ev.title.length * 200 + 1000);
            },

            onSuggestedQuestionsPending: ev => {
              const target = resolveSuggestedQuestionsTarget(ev.message_id);
              if (!target) return;
              dispatch(applySuggestedQuestionsPending({
                ...target,
                revision: ev.revision,
              }));
            },

            onSuggestedQuestionsReady: ev => {
              const target = resolveSuggestedQuestionsTarget(ev.message_id);
              if (!target) return;
              dispatch(applySuggestedQuestionsReady({
                ...target,
                revision: ev.revision,
                status: ev.status,
                questions: ev.questions,
              }));
            },

            onDone: ({ conversationId: incomingConvId }) => {
              if (!isActiveSendCurrent()) return;
              donePayload = { incomingConvId };
              // 标题本身已由 SSE 事件送达，这里只确保草稿已 materialize 后刷新会话列表。
              materializeIfNeeded(incomingConvId);
              const refreshConversationId = serverConvId ?? incomingConvId ?? activeConvIdRef.current;
              if (refreshConversationId) {
                startPostStreamActions(refreshConversationId);
              }
              if (!assistantHasContentRef.current) {
                // 没有文本内容，直接完成（打字机从未启动）
                doCompleteStream(donePayload);
              } else {
                typewriterRef.current.markNetworkDone();
              }
            },

            onError: (message, payload) => {
              if (!isActiveSendCurrent()) return;
              // 没有结构化 payload 的 error 来自 EOF/网络传输层，会进入有限自动续传；
              // 续传成功前不向用户闪现全局错误。
              if (!payload) return;
              // stream_interrupted 是后端确认停止后的终态，由外层 catch 走
              // “生成已停止 + 权威水合”路径，不应短暂闪现错误提示。
              if (isInterruptedStreamSignal(payload)) return;
              const readableMessage = normalizeSendErrorMessage(message);
              dispatch(setGlobalError(readableMessage));
              const errorConvId = activeConvIdRef.current;
              if (errorConvId && ownsSlot()) {
                dispatch(setStreamError({
                  conversationId: errorConvId,
                  message: readableMessage,
                  code: payload?.code,
                  data: payload?.data,
                }));
              }
            },
          };

      try {
        await runResumableStream({
          callbacks: streamCallbacks,
          signal: controller.signal,
          openInitial: (wrappedCallbacks, signal) => sendMessageStream(
            {
              model_id: enabledModel.id,
              message: content.trim(),
              conversation_id: tempConvId,
              user_message_id: userMessageId,
              assistant_message_id: assistantMessageId,
              retry_user_message_id: options.retryUserMessageId,
              retry_assistant_message_id: options.retryAssistantMessageId,
              previous_run_id: options.previousRunId,
              stream: true,
              options: {
                use_reasoning: useReasoning,
                plan_mode: agentModeResolution.planMode,
                task_mode: agentModeResolution.taskMode,
              },
              file_ids: fileIds,
              knowledge_base_ids: options.knowledgeBaseIds,
            },
            wrappedCallbacks,
            signal,
          ),
          openReconnect: async (lastEntryId, wrappedCallbacks, signal) => {
            await reconnectStream(
              activeConvIdRef.current ?? tempConvId,
              lastEntryId,
              wrappedCallbacks,
              signal,
            );
          },
          onPhaseChange: phase => {
            const phaseConvId = activeConvIdRef.current;
            if (!isActiveSendCurrent() || !phaseConvId) return;
            dispatch(setStreamStatus({ conversationId: phaseConvId, status: phase }));
          },
        });
        return;
      } catch (error) {
        typewriterRef.current.stop();
        if (controller.signal.aborted || !isActiveSendCurrent()) {
          // 另一处页面中止了这条流时，本实例的 generation 可能还没作废。
          // 先作废再返回，迟到的 run 事件不能落到下一轮。
          if (controller.signal.aborted && isActiveSendCurrent()) {
            sendGenerationRef.current += 1;
            activeSendContextRef.current = null;
            activeConvIdRef.current = null;
            userMessageIdRef.current = null;
            assistantMessageIdRef.current = null;
            serverMessageIdRef.current = null;
            serverTaskIdRef.current = null;
            assistantHasContentRef.current = false;
            activeRetryTurnSnapshotRef.current = null;
            abortControllerRef.current = null;
          }
          return;
        }

        const effectiveConvIdOnError = activeConvIdRef.current ?? tempConvId;
        if (isInterruptedStreamSignal(error)) {
          clearFirstTurnContextState(effectiveConvIdOnError);
          // 用户可能从导航后的 ChatPage 实例发起停止，原始发送实例无法共享
          // AbortController，只会收到后端持久化后的 stream_interrupted 终态。
          // 这代表“生成已停止”，不是“用户消息发送失败”。
          const streamState = getSlot(effectiveConvIdOnError);
          const partialBlocks = selectFullStreamContentBlocks(streamState);
          if (isMessageRetry && retryTurnSnapshot?.user) {
            dispatch(replaceMessage({
              conversationId: effectiveConvIdOnError,
              messageId: retryTurnSnapshot.user.id,
              message: retryTurnSnapshot.user,
            }));
            if (retryTurnSnapshot.assistant) {
              dispatch(replaceMessage({
                conversationId: effectiveConvIdOnError,
                messageId: retryTurnSnapshot.assistant.id,
                message: retryTurnSnapshot.assistant,
              }));
            } else {
              dispatch(removeMessage({
                conversationId: effectiveConvIdOnError,
                messageId: assistantMessageId,
              }));
            }
          } else if (partialBlocks.length > 0) {
            dispatch(
              updateMessage({
                conversationId: effectiveConvIdOnError,
                messageId: assistantMessageId,
                patch: { content: partialBlocks },
              })
            );
          }
          if (streamState.currentRun?.status === 'running') {
            dispatch(
              finalizeRun({
                conversationId: effectiveConvIdOnError,
                runId: streamState.currentRun.runId,
                status: 'interrupted',
                reason: 'user_cancelled',
                sequence: streamState.currentRun.lastSequence + 1,
              })
            );
          }
          const interruptedRun = getSlot(effectiveConvIdOnError).currentRun;
          if (interruptedRun?.status === 'interrupted') {
            dispatch(updateMessage({
              conversationId: effectiveConvIdOnError,
              messageId: assistantMessageId,
              patch: { agent_run: interruptedRun },
            }));
          }
          const interruptedHydrationAnchorIds = new Set(
            [userMessageId, assistantMessageId, serverMessageIdRef.current].filter(
              (messageId): messageId is string => Boolean(messageId),
            ),
          );
          const preserveMessagesAfterStoppedTurn = (state: RootState) => (
            messageIdsAfterAnchor(state, effectiveConvIdOnError, interruptedHydrationAnchorIds)
          );
          const reapplyInterruptedSnapshot = () => {
            if (interruptedRun?.status !== 'interrupted') return;
            const messages = store.getState().conversation.byId[effectiveConvIdOnError]?.messages ?? [];
            for (const message of messages) {
              const agentRun = message.agent_run;
              if (
                !agentRun
                || agentRun.runId !== interruptedRun.runId
                || agentRun.status !== 'running'
              ) continue;
              const slot = getSlot(effectiveConvIdOnError);
              if (
                slot.isStreaming
                && slot.messageId === message.id
                && slot.currentRun?.runId !== interruptedRun.runId
              ) continue;
              dispatch(updateMessage({
                conversationId: effectiveConvIdOnError,
                messageId: message.id,
                patch: {
                  agent_run: {
                    ...interruptedRun,
                    messageId: message.id,
                    serverMessageId: message.id,
                    status: 'interrupted',
                  },
                },
              }));
            }
          };
          if (!isMessageRetry || !retryTurnSnapshot?.user) {
            dispatch(updateMessage({
              conversationId: effectiveConvIdOnError,
              messageId: userMessageId,
              patch: { status: null },
            }));
          }
          dispatch(endStream({
            conversationId: effectiveConvIdOnError,
            messageId: assistantMessageIdRef.current,
          }));
          sendGenerationRef.current += 1;
          activeSendContextRef.current = null;
          releaseSendController();
          abortControllerRef.current = null;
          activeConvIdRef.current = null;
          userMessageIdRef.current = null;
          assistantMessageIdRef.current = null;
          serverMessageIdRef.current = null;
          serverTaskIdRef.current = null;
          assistantHasContentRef.current = false;
          activeRetryTurnSnapshotRef.current = null;
          dispatch(requestConversationListRefresh(effectiveConvIdOnError));
          void hydrateAuthoritativeConversation(
            effectiveConvIdOnError,
            isSessionCurrent,
            { extraPreserveMessageIds: preserveMessagesAfterStoppedTurn },
          ).then(() => {
            if (isSessionCurrent()) reapplyInterruptedSnapshot();
          });
          // /stop 先完成跨 worker 的 Redis CAS，再由被取消任务异步把
          // Agent run 落为 interrupted。第一次详情读取可能恰好读到 running；
          // 短暂等待后再取一次，避免必须刷新页面才能看到“计划已停止”。
          setTimeout(() => {
            if (!isSessionCurrent()) return;
            void hydrateAuthoritativeConversation(
              effectiveConvIdOnError,
              isSessionCurrent,
              { extraPreserveMessageIds: preserveMessagesAfterStoppedTurn },
            ).then(() => {
              if (isSessionCurrent()) reapplyInterruptedSnapshot();
            });
          }, INTERRUPTED_HYDRATION_RETRY_MS);
          return;
        }

        clearFirstTurnContextState(effectiveConvIdOnError);
        const reconnectRetriesExhausted = isRecoverableStreamError(error);
        const requestAccepted = materializedOnce || Boolean(serverMessageIdRef.current);

        if (!requestAccepted && previousKnowledgeSelection) {
          dispatch(updateConversationKnowledgeBaseIds({
            id: tempConvId,
            knowledgeBaseIds: previousKnowledgeSelection.knowledgeBaseIds,
            updatedAt: previousKnowledgeSelection.updatedAt,
          }));
        }

        const shouldRestoreRetryAnswer = Boolean(
          isMessageRetry
          && retryTurnSnapshot?.user
          && retryTurnSnapshot.assistant,
        );
        if (shouldRestoreRetryAnswer && retryTurnSnapshot?.user && retryTurnSnapshot.assistant) {
          dispatch(replaceMessage({
            conversationId: effectiveConvIdOnError,
            messageId: retryTurnSnapshot.user.id,
            message: retryTurnSnapshot.user,
          }));
          const assistantStillPresent = store.getState().conversation.byId[
            effectiveConvIdOnError
          ]?.messages.some((message) => message.id === retryTurnSnapshot.assistant?.id);
          if (assistantStillPresent) {
            dispatch(replaceMessage({
              conversationId: effectiveConvIdOnError,
              messageId: retryTurnSnapshot.assistant.id,
              message: retryTurnSnapshot.assistant,
            }));
          } else {
            dispatch(appendMessage({
              conversationId: effectiveConvIdOnError,
              message: retryTurnSnapshot.assistant,
            }));
          }
        }

        if (assistantHasContentRef.current && !shouldRestoreRetryAnswer) {
          // 保留已有的 stream content blocks
          const streamState = getSlot(activeConvIdRef.current ?? tempConvId);
          const partialBlocks = reconnectRetriesExhausted
            ? selectFullStreamContentBlocks(streamState)
            : selectStreamContentBlocks(streamState);
          dispatch(
            updateMessage({
              conversationId: effectiveConvIdOnError,
              messageId: assistantMessageId,
              patch: { content: partialBlocks },
            })
          );
        }

        const preservePartialResponse = requestAccepted
          || (reconnectRetriesExhausted && assistantHasContentRef.current);
        if (isDraft && serverConvId && !materializedOnce) {
          materializedOnce = true;
        }
        const effectiveConvId = activeConvIdRef.current ?? tempConvId;
        if (preservePartialResponse) {
          dispatch(
            updateMessage({
              conversationId: effectiveConvId,
              messageId: userMessageId,
              patch: { status: null },
            }),
          );
          if (requestAccepted) {
            void hydrateAuthoritativeConversation(effectiveConvId, isSessionCurrent);
          }
        } else if (isMessageRetry && retryTurnSnapshot?.user) {
          dispatch(replaceMessage({
            conversationId: effectiveConvId,
            messageId: retryTurnSnapshot.user.id,
            message: retryTurnSnapshot.user,
          }));
          if (retryTurnSnapshot.assistant) {
            const assistantStillPresent = store.getState().conversation.byId[
              effectiveConvId
            ]?.messages.some((message) => message.id === retryTurnSnapshot.assistant?.id);
            if (assistantStillPresent) {
              dispatch(replaceMessage({
                conversationId: effectiveConvId,
                messageId: retryTurnSnapshot.assistant.id,
                message: retryTurnSnapshot.assistant,
              }));
            } else {
              dispatch(appendMessage({
                conversationId: effectiveConvId,
                message: retryTurnSnapshot.assistant,
              }));
            }
          } else {
            dispatch(
              removeMessage({ conversationId: effectiveConvId, messageId: assistantMessageId })
            );
          }
        } else if (materializedOnce || !isDraft) {
          dispatch(
            updateMessage({
              conversationId: effectiveConvId,
              messageId: userMessageId,
              patch: { status: 'failed' },
            })
          );
          dispatch(
            removeMessage({ conversationId: effectiveConvId, messageId: assistantMessageId })
          );
        } else {
          dispatch(removeConversation(tempConvId));
          dispatch(setPendingConversationId(null));
        }
        if (shouldRestoreRetryAnswer) {
          dispatch(clearCurrentRun({ conversationId: effectiveConvId }));
        }
        dispatch(endStream({
          conversationId: effectiveConvId,
          messageId: assistantMessageIdRef.current,
        }));
        sendGenerationRef.current += 1;
        activeSendContextRef.current = null;
        releaseSendController();
        abortControllerRef.current = null;
        activeConvIdRef.current = null;
        userMessageIdRef.current = null;
        assistantMessageIdRef.current = null;
        serverMessageIdRef.current = null;
        serverTaskIdRef.current = null;
        assistantHasContentRef.current = false;
        activeRetryTurnSnapshotRef.current = null;
        const message = normalizeSendErrorMessage(error instanceof Error ? error.message : '发送失败，请重试');
        dispatch(setGlobalError(message));
        if (reconnectRetriesExhausted) {
          dispatch(setStreamError({ conversationId: effectiveConvId, message }));
        }
      }
    },
    [
      dispatch,
      composerAgentMode,
      getSlot,
      hydrateAuthoritativeConversation,
      reasoningEnabled,
      stopStreaming,
      store,
    ]
  );

  const retryMessage = useRetryMessage(sendMessage, activeConversationId);

  return { sendMessage, stopStreaming, retryMessage };
}
