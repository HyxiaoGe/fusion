'use client';

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Files } from 'lucide-react';
import { ChatMessageListLazy } from '@/components/lazy/LazyComponents';
import ChatInput, { type ChatUploadCompleteFile } from '@/components/chat/ChatInput';
import type { KnowledgeSelectionStatus } from '@/components/chat/KnowledgeBaseComposerControl';
import {
  getKnowledgeBaseCatalogSnapshot,
  resolveKnowledgeBaseSelectionStatus,
} from '@/lib/chat/knowledgeBaseCatalogResource';
import { ChatDetailOverlayProvider } from '@/components/chat/ChatDetailOverlayContext';
import ConversationFilesPanel from '@/components/chat/ConversationFilesPanel';
import LocationContextBanner from '@/components/chat/LocationContextBanner';
import {
  tryConversationFileToComposerAttachment,
  type ConversationComposerAttachment,
} from '@/components/chat/composerAttachments';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import TrajectoryTabView from '@/components/chat/trajectory/TrajectoryTabView';
import type { FileAttachment } from '@/lib/utils/fileHelpers';
import ConfirmDialog from '@/components/ui/confirm-dialog';
import { useAppDispatch, useAppSelector } from '@/redux/hooks';
import { selectAuthSessionKey, selectIsAuthenticated } from '@/redux/selectors';
import { useStore } from 'react-redux';
import {
  applySuggestedQuestionsPending,
  applySuggestedQuestionsReady,
  appendMessage,
  clearConversationMessages,
  removeMessage,
  requestSuggestedQuestionsObservation,
  setLastReadyConversationSnapshot,
  updateConversationTitle,
  updateMessage,
} from '@/redux/slices/conversationSlice';
import {
  advanceTypewriter,
  appendTextDelta,
  appendThinkingDelta,
  completeThinkingPhase,
  endStream,
  finalizeRun,
  setRunStopConfirmation,
  setStreamError,
  selectFullStreamContentBlocks,
  ownsStreamSlot,
  selectStreamSlot,
  setStreamStatus,
  startStream,
} from '@/redux/slices/streamSlice';
import type { AgentRunState, AgentRunStatus } from '@/types/agentRun';
import type { StreamState } from '@/redux/slices/streamSlice';
import { verifyStoppedRun, withStopDeadline } from '@/lib/chat/stopVerification';
import {
  clearStopOutcomeNotice,
  readStopOutcomeNotice,
  saveStopOutcomeNotice,
  type StopOutcomeNotice,
} from '@/lib/chat/stopOutcomeNotice';
import { fetchStreamStatus } from '@/lib/api/streamStatus';
import { reconnectStream, stopStream, type StreamCallbacks } from '@/lib/api/chat';
import { runResumableStream } from '@/lib/api/resumableStream';
import { useConversation } from '@/hooks/useConversation';
import { useContinueAgentRun } from '@/hooks/useContinueAgentRun';
import { useSendMessage } from '@/hooks/useSendMessage';
import { useSuggestedQuestions } from '@/hooks/useSuggestedQuestions';
import { useSuggestedQuestionContinuation } from '@/hooks/useSuggestedQuestionContinuation';
import { useTransientCompletionState } from '@/hooks/useTransientCompletionState';
import { useConversationFiles } from '@/hooks/useConversationFiles';
import { createAgentStreamEventHandlers } from '@/lib/agent/streamEventHandlers';
import { consumeConversationFilesPanelOpen } from '@/lib/chat/filesPanelHandoff';
import { clearFirstTurnContextState } from '@/lib/chat/contextStatusPersistence';
import { getChatMessageDomId } from '@/lib/chat/messageDom';
import {
  recoverReasoningOnlyFinalBlocks,
  shouldRecoverReasoningOnlyFinalBlocks,
} from '@/lib/chat/contentBlocks';
import { hasFormalTextContent } from '@/lib/chat/suggestedQuestionState';
import {
  getStreamController,
  registerStreamController,
  releaseStreamController,
  updateStreamController,
} from '@/lib/chat/streamControllerRegistry';
import { CHAT_NEW_PATH } from '@/lib/routes/chatRoutes';
import { deleteFile, type FileInfo } from '@/lib/api/files';
import {
  requestTrajectoryInspect,
  selectTrajectoryViewState,
  setTrajectoryActiveSurface,
} from '@/redux/slices/trajectorySlice';

const CHAT_EMPTY_STATE = {
  title: '这个会话还没有消息',
  description: '发送第一条消息，继续这段会话。',
};

const EMPTY_CONVERSATION_ATTACHMENTS: ConversationComposerAttachment[] = [];
const STREAM_STATUS_MAX_ATTEMPTS = 3;
const STREAM_STATUS_RETRY_BASE_DELAY_MS = 50;

const STOP_OUTCOME_LABELS: Record<Exclude<AgentRunStatus, 'running'>, string> = {
  interrupted: '原运行现已中断',
  completed: '原运行现已完成',
  failed: '原运行已失败',
  incomplete: '原运行未完整完成',
  limit_reached: '原运行已达到限制',
};

function isAbortError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: string }).name === 'AbortError';
}

function isRecoverableStreamStatusError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const candidate = error as { recoverable?: boolean; code?: string; statusCode?: number };
  return candidate.recoverable === true ||
    candidate.code === 'redis_read_failed' ||
    (typeof candidate.statusCode === 'number' && candidate.statusCode >= 500);
}

function waitForStreamStatusRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timeoutId = window.setTimeout(() => {
      signal.removeEventListener('abort', handleAbort);
      resolve();
    }, delayMs);
    const handleAbort = () => {
      window.clearTimeout(timeoutId);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal.addEventListener('abort', handleAbort, { once: true });
  });
}

interface ConversationAttachmentState {
  chatId: string;
  attachments: ConversationComposerAttachment[];
}

interface PendingAutoAttachState {
  chatId: string;
  fileIds: string[];
}

function uploadResultToConversationAttachment(file: ChatUploadCompleteFile): ConversationComposerAttachment | null {
  if (file.status !== 'processed') {
    return null;
  }

  return {
    source: 'conversation',
    fileId: file.fileId,
    filename: file.filename,
    mimetype: file.mimetype || 'application/octet-stream',
    status: 'processed',
    thumbnailUrl: file.thumbnailUrl ?? null,
    removeBehavior: 'delete',
  };
}

export default function ChatPage() {
  const params = useParams();
  const router = useRouter();
  const dispatch = useAppDispatch();
  const store = useStore();
  const chatId = params?.chatId as string;
  const latestChatIdRef = useRef(chatId);
  latestChatIdRef.current = chatId;
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);
  const [filesPanelConversationId, setFilesPanelConversationId] = useState<string | null>(null);
  const filesPanelOpen = filesPanelConversationId === chatId;
  const [conversationAttachmentState, setConversationAttachmentState] = useState<ConversationAttachmentState>({
    chatId,
    attachments: [],
  });
  const [pendingAutoAttachState, setPendingAutoAttachState] = useState<PendingAutoAttachState>({
    chatId,
    fileIds: [],
  });
  const chatInputRef = useRef<HTMLDivElement>(null);
  const [trajectoryComposerInset, setTrajectoryComposerInset] = useState(256);
  const trajectoryInspectSequenceRef = useRef(0);
  const reconnectControllerRef = useRef<AbortController | null>(null);
  // 切会话不再掐断恢复流，所以离开聊天页时没人收尾了：这里记住本页面还活着的
  // 恢复流，真正卸载时统一停写并中止，避免留下无人认领的 SSE。
  const liveRecoveryStreamsRef = useRef<Set<{ controller: AbortController; detach: () => void }>>(new Set());
  const recoveryStopPendingRef = useRef<{
    controller: AbortController;
    bufferedActions: Array<() => void>;
    streamTerminated: boolean;
  } | null>(null);
  const confirmedRecoveryStopsRef = useRef(new Map<string, {
    conversationId: string;
    sessionKey: string | null;
    run: AgentRunState;
  }>());
  const isAuthenticated = useAppSelector(selectIsAuthenticated);
  const authSessionKey = useAppSelector(selectAuthSessionKey);
  const latestAuthSessionKeyRef = useRef(authSessionKey);
  latestAuthSessionKeyRef.current = authSessionKey;
  const [stopOutcomeNotice, setStopOutcomeNotice] = useState<StopOutcomeNotice | null>(null);
  const { conversation, hydrationView, hydrationError, retryHydration } = useConversation(chatId);
  useEffect(() => {
    const notice = readStopOutcomeNotice(chatId, authSessionKey);
    setStopOutcomeNotice(notice);
    if (!notice || notice.terminalStatus) return;
    const controller = new AbortController();
    void verifyStoppedRun(
      chatId,
      notice.runId,
      notice.messageId,
      () => !controller.signal.aborted && latestChatIdRef.current === chatId
        && latestAuthSessionKeyRef.current === authSessionKey,
      controller.signal,
    ).then((terminalStatus) => {
      if (!terminalStatus || controller.signal.aborted) return;
      const current = readStopOutcomeNotice(chatId, authSessionKey);
      if (current?.runId !== notice.runId) return;
      const resolved = { ...current, terminalStatus };
      saveStopOutcomeNotice(resolved);
      setStopOutcomeNotice(resolved);
    });
    return () => controller.abort();
  }, [authSessionKey, chatId]);
  // /stop 确认可能先于详情落库。仅修正同一消息、同一 run 的旧 running 快照。
  useEffect(() => {
    for (const message of conversation?.messages ?? []) {
      const confirmed = confirmedRecoveryStopsRef.current.get(message.id);
      if (!confirmed || confirmed.conversationId !== chatId || confirmed.sessionKey !== authSessionKey) continue;
      if (message.agent_run?.runId !== confirmed.run.runId || message.agent_run.status !== 'running') continue;
      const slot = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
      if (slot.isStreaming && slot.messageId === message.id && slot.currentRun?.runId !== confirmed.run.runId) continue;
      dispatch(updateMessage({
        conversationId: chatId,
        messageId: message.id,
        patch: { agent_run: confirmed.run },
      }));
    }
  }, [authSessionKey, chatId, conversation?.messages, dispatch, store]);
  const [composerKnowledgeSelection, setComposerKnowledgeSelection] = useState<{
    chatId: string;
    ids: string[];
    status: KnowledgeSelectionStatus;
  }>({ chatId, ids: [], status: 'ready' });
  const { sendMessage, stopStreaming, retryMessage } = useSendMessage(chatId);
  const { stopContinueAgentRun } = useContinueAgentRun();
  const {
    suggestedQuestions,
    isLoadingQuestions,
    fetchQuestions,
    clearQuestions,
  } = useSuggestedQuestions(chatId);
  const {
    files: conversationFiles,
    isLoading: conversationFilesLoading,
    error: conversationFilesError,
    refresh: refreshConversationFiles,
    removeFile: removeConversationFile,
  } = useConversationFiles(chatId, {
    enabled: isAuthenticated,
    sessionKey: authSessionKey,
  });
  const conversationError = useAppSelector((state) => state.conversation.globalError);
  // 「正在生成」现在是每个会话各自的事实，不再是全局标志。
  const isStreaming = useAppSelector((state) => selectStreamSlot(state, chatId).isStreaming);
  const lastReadyConversationSnapshot = useAppSelector(
    (state) => state.conversation.lastReadyConversationSnapshot
  );
  const activeSurface = useAppSelector((state) => (
    selectTrajectoryViewState(state, chatId)?.activeSurface ?? 'chat'
  ));
  const conversationMessages = conversation?.messages;
  const composerKnowledgeBaseIds = useMemo(
    () => composerKnowledgeSelection.chatId === chatId
      ? composerKnowledgeSelection.ids
      : (conversation?.knowledge_base_ids ?? []),
    [
      chatId,
      composerKnowledgeSelection.chatId,
      composerKnowledgeSelection.ids,
      conversation?.knowledge_base_ids,
    ],
  );
  const composerKnowledgeSelectionStatus = composerKnowledgeSelection.chatId === chatId
    ? composerKnowledgeSelection.status
    : 'loading';

  useLayoutEffect(() => {
    if (activeSurface !== 'trajectory') return;
    const composer = chatInputRef.current;
    if (!composer) return;
    const updateInset = () => {
      const height = Math.ceil(composer.getBoundingClientRect().height);
      if (height > 0) setTrajectoryComposerInset(current => current === height ? current : height);
    };
    updateInset();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(updateInset);
    observer.observe(composer);
    return () => observer.disconnect();
  }, [activeSurface]);

  useEffect(() => {
    if (hydrationView !== 'ready') return;
    setComposerKnowledgeSelection({
      chatId,
      ids: conversation?.knowledge_base_ids ?? [],
      status: resolveKnowledgeBaseSelectionStatus(
        getKnowledgeBaseCatalogSnapshot(authSessionKey),
        conversation?.knowledge_base_ids ?? [],
      ),
    });
  }, [authSessionKey, chatId, conversation?.knowledge_base_ids, hydrationView]);

  useEffect(() => {
    clearQuestions();
  }, [chatId, clearQuestions]);

  useEffect(() => {
    setFilesPanelConversationId(consumeConversationFilesPanelOpen(chatId) ? chatId : null);
    setConversationAttachmentState((current) => {
      if (current.chatId === chatId && current.attachments.length === 0) {
        return current;
      }
      return { chatId, attachments: [] };
    });
    setPendingAutoAttachState((current) => {
      if (current.chatId === chatId && current.fileIds.length === 0) {
        return current;
      }
      return { chatId, fileIds: [] };
    });
  }, [chatId]);

  useEffect(() => {
    if (hydrationView !== 'ready' || !conversationMessages) {
      return;
    }

    dispatch(setLastReadyConversationSnapshot({
      chatId,
      messages: [...conversationMessages],
    }));
  }, [chatId, conversationMessages, dispatch, hydrationView]);

  // 页面 mount / hydration 完成后检查是否有未完成的流，并在可恢复中断时有限重连。
  const hydrationDone = hydrationView === 'ready';
  const reconnectAttemptedRef = useRef(false);
  // chatId 变化时重置
  useEffect(() => {
    reconnectAttemptedRef.current = false;
  }, [chatId]);
  useEffect(() => {
    // 只拦"本会话已经在生成"。isStreaming 现在就是本会话槽位的字段，跨会话不再互相影响
    // ——此前它是全局标志，从正在生成的会话 A 切到 B 会把 B 的未完成流检查整个跳过（issue #74）。
    if (!chatId || !isAuthenticated || !hydrationDone) return;
    if (isStreaming) return;
    // 每个 chatId 只尝试一次重连，防止 stop 后重复触发
    if (reconnectAttemptedRef.current) return;
    reconnectAttemptedRef.current = true;

    let cancelled = false;
    const controller = new AbortController();
    reconnectControllerRef.current?.abort();
    reconnectControllerRef.current = controller;
    // 恢复流登记在本会话名下：停止时按会话查表命中它，而不是靠「ref 非空」推断。
    // stream_mode / task_id 随后由 updateStreamController 补进同一条目。
    registerStreamController({ conversationId: chatId, kind: 'recovery', controller });
    const liveRecoveryStream = { controller, detach: () => { cancelled = true; } };
    liveRecoveryStreamsRef.current.add(liveRecoveryStream);
    // 外层 catch 够不到 try 内的 messageId，但 endStream 需要归属：在这里记住本次恢复的那条消息。
    let recoveredMessageId: string | null = null;
    // 同一会话内本会话的新一轮发送会换掉槽位；换掉后再写入或读取都会串到那一轮头上。
    const ownsRecoverySlot = () => ownsStreamSlot(
      selectStreamSlot(store.getState() as { stream: StreamState }, chatId),
      recoveredMessageId,
    );
    const checkAndReconnect = async () => {
      try {
        // 直接查后端流状态，由后端 meta 决定是否重连
        // 用户点停止 → 后端 cancel_stream 设 meta=cancelled → 这里不会返回 streaming
        // 用户切换对话再切回来 → 后台任务仍在跑 → meta=streaming → 自动重连
        let status: Awaited<ReturnType<typeof fetchStreamStatus>> | null = null;
        for (let attempt = 1; attempt <= STREAM_STATUS_MAX_ATTEMPTS; attempt += 1) {
          try {
            status = await fetchStreamStatus(chatId, controller.signal);
            break;
          } catch (error) {
            if (isAbortError(error) || controller.signal.aborted || cancelled) return;
            if (!isRecoverableStreamStatusError(error) || attempt === STREAM_STATUS_MAX_ATTEMPTS) {
              throw error;
            }
            await waitForStreamStatusRetry(STREAM_STATUS_RETRY_BASE_DELAY_MS * attempt, controller.signal);
          }
        }
        if (cancelled || !status || status.status !== 'streaming') return;
        updateStreamController(chatId, controller, {
          streamMode: status.stream_mode ?? 'initial',
          taskId: status.task_id ?? null,
        });

        const messageId = status.message_id || '';
        recoveredMessageId = messageId;

        // 确保有 assistant 消息占位
        const conv = conversation;
        const existingAssistant = conv?.messages?.find((m) => m.role === 'assistant' && m.id === messageId);
        const continuationStaticBlocks = status.stream_mode === 'continuation'
          ? existingAssistant?.content
          : undefined;
        const insertedPlaceholder = !existingAssistant && Boolean(messageId);
        if (insertedPlaceholder) {
          dispatch(appendMessage({
            conversationId: chatId,
            message: { id: messageId, role: 'assistant', content: [], timestamp: Date.now() },
          }));
        }

        // 启动流式状态
        dispatch(startStream({
          conversationId: chatId,
          messageId,
          ...(continuationStaticBlocks ? { staticBlocks: continuationStaticBlocks } : {}),
        }));
        // 有进行中的流 → 建立 SSE 重连，从头读取。
        // 必须排在 startStream 之后：槽位由它建立，之前派发的槽位 action 没有落点会被丢弃。
        dispatch(setStreamStatus({ conversationId: chatId, status: 'reconnecting' }));

        let reachedTerminalState = false;
        let failureFinalized = false;
        const dispatchOrBufferRecoveryAction = (action: () => void) => {
          const pendingStop = recoveryStopPendingRef.current;
          if (pendingStop?.controller === controller) {
            pendingStop.bufferedActions.push(action);
            return;
          }
          action();
        };
        const flushBufferedRecoveryActions = () => {
          const pendingStop = recoveryStopPendingRef.current;
          if (pendingStop?.controller !== controller) return;
          recoveryStopPendingRef.current = null;
          pendingStop.bufferedActions.forEach((action) => action());
        };
        const finalizeRecoveryFailure = () => {
          if (cancelled || failureFinalized) return;
          clearFirstTurnContextState(chatId);
          const pendingStop = recoveryStopPendingRef.current;
          if (pendingStop?.controller === controller) {
            pendingStop.streamTerminated = true;
            reachedTerminalState = true;
            return;
          }
          flushBufferedRecoveryActions();
          failureFinalized = true;
          if (status.stream_mode === 'retry') {
            if (insertedPlaceholder && messageId) {
              dispatch(removeMessage({ conversationId: chatId, messageId }));
            }
            dispatch(endStream({ conversationId: chatId, messageId }));
            dispatch(setStreamStatus({ conversationId: chatId, status: 'error' }));
            retryHydration();
            return;
          }
          const streamState = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
          // 槽位已被本会话新一轮换掉时读到的是那一轮的正文，写下去就是串入这条消息。
          const partialBlocks = ownsRecoverySlot() ? selectFullStreamContentBlocks(streamState) : [];
          if (messageId && partialBlocks.length > 0) {
            dispatch(updateMessage({
              conversationId: chatId,
              messageId,
              patch: { content: partialBlocks },
            }));
          } else if (insertedPlaceholder && messageId) {
            dispatch(removeMessage({ conversationId: chatId, messageId }));
          }
          dispatch(endStream({ conversationId: chatId, messageId }));
          dispatch(setStreamStatus({ conversationId: chatId, status: 'error' }));
        };
        const callbacks: StreamCallbacks = {
          onReady: ({ taskId }) => {
            // 没带 taskId 时保留 stream-status 给的那个，别覆盖成空。
            if (taskId) updateStreamController(chatId, controller, { taskId });
          },
          onAnswering: (payload) => {
            if (cancelled) return;
            dispatchOrBufferRecoveryAction(() => {
              const slot = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
              if (slot.isStreamingReasoning) {
                dispatch(completeThinkingPhase({ conversationId: chatId }));
              }
              dispatch(appendTextDelta({
                conversationId: chatId,
                blockId: payload.block_id,
                delta: payload.delta,
                runId: payload.run_id,
                stepId: payload.step_id,
              }));
              dispatch(advanceTypewriter({ conversationId: chatId, chars: payload.delta.length }));
            });
          },
          onReasoning: (payload) => {
            if (cancelled) return;
            dispatchOrBufferRecoveryAction(() => {
              dispatch(appendThinkingDelta({
                conversationId: chatId,
                blockId: payload.block_id,
                delta: payload.delta,
                runId: payload.run_id,
                stepId: payload.step_id,
              }));
            });
          },
          ...createAgentStreamEventHandlers({
            dispatch,
            trajectoryDispatch: dispatch,
            isActive: () => !cancelled && ownsRecoverySlot(),
            resolveMessageId: () => messageId,
            resolveConversationId: () => chatId,
            resolveTrajectoryConversationId: () => chatId,
          }),
          onConversationTitleUpdated: ev => {
            if (cancelled) return;
            dispatchOrBufferRecoveryAction(() => {
              dispatch(updateConversationTitle({
                id: ev.conversation_id,
                title: ev.title,
              }));
            });
          },
          onSuggestedQuestionsPending: ev => {
            if (cancelled) return;
            dispatchOrBufferRecoveryAction(() => {
              dispatch(applySuggestedQuestionsPending({
                conversationId: chatId,
                messageId: ev.message_id,
                localMessageId: messageId,
                revision: ev.revision,
              }));
            });
          },
          onSuggestedQuestionsReady: ev => {
            if (cancelled) return;
            dispatchOrBufferRecoveryAction(() => {
              dispatch(applySuggestedQuestionsReady({
                conversationId: chatId,
                messageId: ev.message_id,
                localMessageId: messageId,
                revision: ev.revision,
                status: ev.status,
                questions: ev.questions,
              }));
            });
          },
          onDone: () => {
            if (cancelled) return;
            flushBufferedRecoveryActions();
            reachedTerminalState = true;
            const streamState = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
            const rawBlocks = ownsRecoverySlot() ? selectFullStreamContentBlocks(streamState) : [];
            const blocks = shouldRecoverReasoningOnlyFinalBlocks({
              runStatus: streamState.currentRun?.status,
              messageMatches: true,
            })
              ? recoverReasoningOnlyFinalBlocks(rawBlocks)
              : rawBlocks;
            if (messageId && blocks.length > 0) {
              dispatch(updateMessage({
                conversationId: chatId,
                messageId,
                patch: { content: blocks },
              }));
              if (hasFormalTextContent(rawBlocks)) {
                dispatch(requestSuggestedQuestionsObservation({
                  conversationId: chatId,
                  messageIds: [messageId],
                }));
              }
            }
            dispatch(endStream({ conversationId: chatId, messageId }));
            dispatch(setStreamStatus({ conversationId: chatId, status: 'completed' }));
            retryHydration();
          },
          onError: () => {
            if (cancelled) return;
            const pendingStop = recoveryStopPendingRef.current;
            if (pendingStop?.controller === controller) {
              pendingStop.streamTerminated = true;
              reachedTerminalState = true;
              return;
            }
            flushBufferedRecoveryActions();
            reachedTerminalState = true;
            finalizeRecoveryFailure();
          },
        };

        try {
          await runResumableStream({
            callbacks,
            signal: controller.signal,
            retryDelaysMs: [250, 750],
            openInitial: async (wrappedCallbacks, signal) => {
              await reconnectStream(chatId, '0', wrappedCallbacks, signal);
            },
            openReconnect: (lastEntryId, wrappedCallbacks, signal) => (
              reconnectStream(chatId, lastEntryId, wrappedCallbacks, signal)
            ),
            onPhaseChange: (phase) => dispatch(setStreamStatus({ conversationId: chatId, status: phase })),
          });
        } catch (error) {
          if (isAbortError(error) || controller.signal.aborted || cancelled) return;
          finalizeRecoveryFailure();
          return;
        }

        if (!reachedTerminalState && !cancelled) {
          finalizeRecoveryFailure();
        }
      } catch (error) {
        if (isAbortError(error)) return;
        if (!cancelled) {
          dispatch(endStream({ conversationId: chatId, messageId: recoveredMessageId }));
          dispatch(setStreamStatus({ conversationId: chatId, status: 'error' }));
        }
      } finally {
        liveRecoveryStreamsRef.current.delete(liveRecoveryStream);
        releaseStreamController(chatId, controller);
        if (reconnectControllerRef.current === controller) {
          reconnectControllerRef.current = null;
        }
      }
    };

    checkAndReconnect();
    return () => {
      // 这里此前会 abort 恢复流并无条件 endStream：全局单槽时切走不清就会让
      // isStreaming 残留为 true、挡住下一个会话的重连。槽位按会话拆开后这条清理
      // 反而是错的——它会在用户切走的瞬间掐断本会话正在跑的生成。
      // 现在切走什么都不做：这条流继续写自己的槽位，切回来时上面的
      // 「本会话已经在生成」直接命中，不会重复建流。
      if (reconnectControllerRef.current === controller) {
        reconnectControllerRef.current = null;
      }
    };
  }, [chatId, isAuthenticated, hydrationDone]); // eslint-disable-line react-hooks/exhaustive-deps

  // 只在真正卸载时收口。依赖数组为空，切 chatId 不会触发。
  useEffect(() => {
    const liveRecoveryStreams = liveRecoveryStreamsRef.current;
    return () => {
      liveRecoveryStreams.forEach(({ controller, detach }) => {
        detach();
        controller.abort();
      });
      liveRecoveryStreams.clear();
    };
  }, []);

  const showCompletionState = useTransientCompletionState({
    isStreaming,
    isLoadingQuestions,
    messages: conversation?.messages || lastReadyConversationSnapshot?.messages || [],
  });

  const handleSendMessage = useCallback((
    content: string,
    attachments?: FileAttachment[],
    _pendingConversationId?: string,
    knowledgeBaseIds?: string[],
    onRejectedBeforeSend?: () => void,
    onAccepted?: () => void,
  ) => {
    clearQuestions();
    if (attachments && attachments.length > 0) {
      setFilesPanelConversationId(chatId);
    }
    return sendMessage(
      content,
      {
        conversationId: chatId,
        knowledgeBaseIds,
        onRejectedBeforeSend,
        onAccepted,
        onStreamEnd: (conversationId) => {
          if (attachments && attachments.length > 0) {
            void refreshConversationFiles(conversationId);
          }
          if (conversationId === latestChatIdRef.current) {
            if (!attachments || attachments.length === 0) {
              void refreshConversationFiles(conversationId);
            }
          }
        },
      },
      attachments
    );
  }, [chatId, clearQuestions, refreshConversationFiles, sendMessage]);

  const handleComposerKnowledgeBaseIdsChange = useCallback((ids: string[]) => {
    setComposerKnowledgeSelection((current) => ({
      chatId,
      ids,
      status: current.chatId === chatId ? current.status : 'loading',
    }));
  }, [chatId]);

  const handleComposerKnowledgeSelectionStatusChange = useCallback((status: KnowledgeSelectionStatus) => {
    setComposerKnowledgeSelection((current) => ({
      chatId,
      ids: current.chatId === chatId
        ? current.ids
        : (conversation?.knowledge_base_ids ?? []),
      status,
    }));
  }, [chatId, conversation?.knowledge_base_ids]);

  const handleSuggestedQuestionSend = useCallback((question: string) => (
    handleSendMessage(question, undefined, undefined, composerKnowledgeBaseIds)
  ), [composerKnowledgeBaseIds, handleSendMessage]);

  const handleSelectQuestion = useSuggestedQuestionContinuation({
    canContinue: Boolean(chatId) && composerKnowledgeSelectionStatus === 'ready',
    clearQuestions,
    sendMessage: handleSuggestedQuestionSend,
    scrollTargetRef: chatInputRef,
  });

  const handleRefreshQuestions = useCallback(() => {
    if (!chatId) return;
    fetchQuestions(true);
  }, [chatId, fetchQuestions]);

  const handleRetry = useCallback(
    (messageId: string) => {
      if (!chatId || composerKnowledgeSelectionStatus !== 'ready') return;
      void retryMessage(messageId, chatId, composerKnowledgeBaseIds);
    },
    [
      chatId,
      composerKnowledgeBaseIds,
      composerKnowledgeSelectionStatus,
      retryMessage,
    ]
  );

  const handleStopStreaming = useCallback(async () => {
    // 按会话查表定位要停的那条流。此前是按「哪个 ref 非空」依次猜
    // （恢复流 ref → 续跑 → 发送流），单会话下同时只有一条流才猜得中；
    // 多会话并发后，在 A 点停止会停掉恰好非空的那条，也就是 B 的流。
    const activeStream = getStreamController(chatId);
    if (activeStream?.kind === 'recovery') {
      const recoveryController = activeStream.controller;
      if (recoveryStopPendingRef.current?.controller === recoveryController) {
        return;
      }
      const streamState = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
      const stoppedMessageId = streamState.messageId;
      const stoppedRunId = streamState.currentRun?.runId;
      const stillOwnsStoppedStream = () => {
        if (latestAuthSessionKeyRef.current !== authSessionKey || recoveryController.signal.aborted) return false;
        const slot = selectStreamSlot(store.getState() as { stream: StreamState }, chatId);
        const currentController = getStreamController(chatId)?.controller;
        // SSE 可能先收到终态并释放注册表，/stop 响应随后才到；仍按原消息核对。
        return (currentController === recoveryController || (!currentController && Boolean(stoppedMessageId) && slot.messageId === stoppedMessageId))
          && ownsStreamSlot(slot, stoppedMessageId)
          && (!stoppedRunId || slot.currentRun?.runId === stoppedRunId);
      };
      const recoveryTaskId = activeStream.taskId ?? null;
      if (!recoveryTaskId) {
        const run = streamState.currentRun;
        if (run?.status === 'running') {
          dispatch(setRunStopConfirmation({
            conversationId: chatId,
            runId: run.runId,
            confirmation: { status: 'unconfirmed', requestedAt: Date.now() },
          }));
          const pendingRun = selectStreamSlot(store.getState() as { stream: StreamState }, chatId).currentRun;
          if (stoppedMessageId && pendingRun?.runId === run.runId) {
            dispatch(updateMessage({ conversationId: chatId, messageId: stoppedMessageId, patch: { agent_run: pendingRun } }));
          }
        } else {
          dispatch(setStreamError({ conversationId: chatId, code: 'stop_unconfirmed', message: '尚未确认停止结果，请稍后重试停止。' }));
        }
        return;
      }
      const stopBoundary = {
        controller: recoveryController,
        bufferedActions: [] as Array<() => void>,
        streamTerminated: false,
      };
      recoveryStopPendingRef.current = stopBoundary;
      const handleRecoveryStopNotApplied = () => {
        if (recoveryStopPendingRef.current !== stopBoundary) return;
        recoveryStopPendingRef.current = null;
        if (!stillOwnsStoppedStream()) return;
        if (
          reconnectControllerRef.current === recoveryController &&
          !recoveryController.signal.aborted &&
          !stopBoundary.streamTerminated
        ) {
          stopBoundary.bufferedActions.forEach((action) => action());
          return;
        }
        dispatch(endStream({ conversationId: chatId, messageId: stoppedMessageId }));
        dispatch(setStreamStatus({ conversationId: chatId, status: 'error' }));
        if (isRetryRecovery) {
          releaseStreamController(chatId, recoveryController);
          retryHydration();
        }
      };
      const isRetryRecovery = activeStream.streamMode === 'retry';
      const partialBlocks = isRetryRecovery ? [] : selectFullStreamContentBlocks(streamState);
      if (!isRetryRecovery && streamState.messageId && partialBlocks.length > 0) {
        dispatch(updateMessage({
          conversationId: chatId,
          messageId: streamState.messageId,
          patch: { content: partialBlocks },
        }));
      }
      const markConfirmation = (status: 'pending' | 'unconfirmed') => {
        if (!stillOwnsStoppedStream()) return;
        if (status === 'unconfirmed' && authSessionKey && stoppedRunId) {
          saveStopOutcomeNotice({
            authIdentity: authSessionKey,
            conversationId: chatId,
            runId: stoppedRunId,
            messageId: stoppedMessageId,
            requestedAt: Date.now(),
            terminalStatus: null,
          });
        }
        const run = selectStreamSlot(store.getState() as { stream: StreamState }, chatId).currentRun;
        if (run?.status === 'running' && run.runId === stoppedRunId && !isRetryRecovery) {
          const confirmation = { status, requestedAt: Date.now() };
          dispatch(setRunStopConfirmation({ conversationId: chatId, runId: run.runId, confirmation }));
          if (stoppedMessageId) {
            const messageRun = store.getState().conversation.byId[chatId]?.messages.find(message => message.id === stoppedMessageId)?.agent_run;
            if (!messageRun || (messageRun.runId === run.runId && messageRun.status === 'running')) {
              dispatch(updateMessage({ conversationId: chatId, messageId: stoppedMessageId, patch: { agent_run: { ...run, stopConfirmation: confirmation } } }));
            }
          }
        } else if (status === 'unconfirmed' && (!run || isRetryRecovery)) {
          dispatch(setStreamError({ conversationId: chatId, code: 'stop_unconfirmed', message: '已停止接收回答，但未确认后台是否停止。请刷新会话核实。' }));
        }
      };
      try {
        let terminalStatus: Exclude<AgentRunStatus, 'running'> | null = null;
        let requestTimedOut = false;
        try {
          const cancelled = await withStopDeadline(signal => stopStream(
            chatId, stoppedMessageId ?? undefined, signal, partialBlocks, recoveryTaskId,
          ), 500, recoveryController.signal);
          if (!cancelled) {
            handleRecoveryStopNotApplied();
            return;
          }
          terminalStatus = 'interrupted';
        } catch (error) {
          if (!(error instanceof DOMException) || error.name !== 'TimeoutError') throw error;
          requestTimedOut = true;
          markConfirmation('pending');
          if (stoppedRunId && stillOwnsStoppedStream()) {
            terminalStatus = await verifyStoppedRun(chatId, stoppedRunId, stoppedMessageId, stillOwnsStoppedStream, recoveryController.signal);
          }
          // 并行详情水合已经拿到终态时，不能再降级成“未确认”。
          const knownRun = store.getState().conversation.byId[chatId]?.messages.find(message => message.id === stoppedMessageId)?.agent_run;
          if (knownRun?.runId === stoppedRunId && knownRun.status !== 'running') terminalStatus = knownRun.status;
        }
        if (recoveryStopPendingRef.current !== stopBoundary) {
          return;
        }
        recoveryStopPendingRef.current = null;
        const stillOwned = stillOwnsStoppedStream();
        if (stillOwned) {
          if (terminalStatus && stoppedRunId) {
            clearStopOutcomeNotice(chatId, stoppedRunId);
            setStopOutcomeNotice(current => current?.conversationId === chatId && current.runId === stoppedRunId
              ? null : current);
          }
          const run = selectStreamSlot(store.getState() as { stream: StreamState }, chatId).currentRun;
          if (terminalStatus && !isRetryRecovery && stoppedRunId && run?.runId === stoppedRunId && run.status === 'running') {
            dispatch(finalizeRun({ conversationId: chatId, runId: stoppedRunId, status: terminalStatus, reason: terminalStatus === 'interrupted' ? 'user_cancelled' : undefined, sequence: run.lastSequence + 1 }));
            const finalized = selectStreamSlot(store.getState() as { stream: StreamState }, chatId).currentRun;
            if (stoppedMessageId && finalized?.runId === stoppedRunId && finalized.status === terminalStatus) {
              confirmedRecoveryStopsRef.current.set(stoppedMessageId, { conversationId: chatId, sessionKey: authSessionKey, run: finalized });
              dispatch(updateMessage({ conversationId: chatId, messageId: stoppedMessageId, patch: { agent_run: finalized } }));
            }
          }
        }
        if (requestTimedOut && !terminalStatus) markConfirmation('unconfirmed');
        liveRecoveryStreamsRef.current.forEach((live) => {
          if (live.controller === recoveryController) {
            live.detach();
            liveRecoveryStreamsRef.current.delete(live);
          }
        });
        releaseStreamController(chatId, recoveryController);
        recoveryController.abort();
        if (reconnectControllerRef.current === recoveryController) {
          reconnectControllerRef.current = null;
        }
        if (stillOwned) {
          clearFirstTurnContextState(chatId);
          dispatch(endStream({ conversationId: chatId, messageId: stoppedMessageId }));
          if ((!requestTimedOut || terminalStatus) && latestChatIdRef.current === chatId) retryHydration();
        }
      } catch (error) {
        if (recoveryController.signal.aborted) {
          if (recoveryStopPendingRef.current === stopBoundary) recoveryStopPendingRef.current = null;
          return;
        }
        console.warn('[chat] 停止恢复流并持久化部分内容失败', error);
        handleRecoveryStopNotApplied();
      }
      return;
    }
    // 续跑同样按会话精确停止；stopContinueAgentRun 过去靠返回 bool 表达
    // 「这条归我管」，是个隐式协议，现在由注册表的 kind 显式决定走哪条路。
    if (activeStream?.kind === 'continuation') {
      if (await stopContinueAgentRun(chatId)) return;
    }
    await stopStreaming(chatId);
  }, [authSessionKey, chatId, dispatch, retryHydration, stopContinueAgentRun, stopStreaming, store]);

  const handleClearChat = () => {
    if (!chatId) return;
    setConfirmDialogOpen(true);
  };

  const confirmClearChat = useCallback(() => {
    dispatch(clearConversationMessages(chatId));
  }, [chatId, dispatch]);

  const conversationAttachments = conversationAttachmentState.chatId === chatId
    ? conversationAttachmentState.attachments
    : EMPTY_CONVERSATION_ATTACHMENTS;
  const shouldShowFilesPanelButton =
    filesPanelOpen ||
    conversationFiles.length > 0 ||
    conversationAttachments.length > 0 ||
    Boolean(conversationFilesError);

  const addConversationAttachment = useCallback((attachment: ConversationComposerAttachment) => {
    setConversationAttachmentState((currentState) => {
      const currentAttachments = currentState.chatId === chatId ? currentState.attachments : [];
      if (currentAttachments.some((item) => item.fileId === attachment.fileId)) {
        return currentState.chatId === chatId ? currentState : { chatId, attachments: currentAttachments };
      }
      return { chatId, attachments: [...currentAttachments, attachment] };
    });
  }, [chatId]);

  const handleAddConversationFile = useCallback((file: FileInfo) => {
    const attachment = tryConversationFileToComposerAttachment(file);
    if (!attachment) {
      return;
    }

    addConversationAttachment(attachment);
  }, [addConversationAttachment]);

  const handleRemoveConversationAttachment = useCallback((fileId: string) => {
    const targetAttachment = conversationAttachments.find((item) => item.fileId === fileId);
    setConversationAttachmentState((currentState) => {
      if (currentState.chatId !== chatId) {
        return currentState;
      }
      const nextAttachments = currentState.attachments.filter((item) => item.fileId !== fileId);
      return nextAttachments.length === currentState.attachments.length
        ? currentState
        : { chatId, attachments: nextAttachments };
    });

    if (targetAttachment?.removeBehavior === 'delete') {
      void deleteFile(fileId)
        .then(() => {
          removeConversationFile(fileId, chatId);
          setPendingAutoAttachState((currentState) => {
            if (currentState.chatId !== chatId || !currentState.fileIds.includes(fileId)) {
              return currentState;
            }
            return {
              chatId,
              fileIds: currentState.fileIds.filter((item) => item !== fileId),
            };
          });
        })
        .catch((error) => {
          console.error('删除会话资料失败:', error);
          void refreshConversationFiles(chatId);
        });
    }
  }, [chatId, conversationAttachments, refreshConversationFiles, removeConversationFile]);

  const handleClearConversationAttachments = useCallback(() => {
    setConversationAttachmentState((currentState) => {
      if (currentState.chatId === chatId && currentState.attachments.length === 0) {
        return currentState;
      }
      return { chatId, attachments: [] };
    });
  }, [chatId]);

  const handleDeleteConversationFile = useCallback((fileId: string) => {
    void deleteFile(fileId)
      .then(() => {
        removeConversationFile(fileId, chatId);
        setConversationAttachmentState((currentState) => {
          if (currentState.chatId !== chatId) {
            return currentState;
          }
          return {
            chatId,
            attachments: currentState.attachments.filter((item) => item.fileId !== fileId),
          };
        });
        setPendingAutoAttachState((currentState) => {
          if (currentState.chatId !== chatId || !currentState.fileIds.includes(fileId)) {
            return currentState;
          }
          return {
            chatId,
            fileIds: currentState.fileIds.filter((item) => item !== fileId),
          };
        });
      })
      .catch((error) => {
        console.error('删除会话资料失败:', error);
        void refreshConversationFiles(chatId);
      });
  }, [chatId, refreshConversationFiles, removeConversationFile]);

  const handleUploadComplete = useCallback((files: ChatUploadCompleteFile[] = [], uploadChatId = chatId) => {
    void refreshConversationFiles(uploadChatId);

    if (uploadChatId !== chatId) {
      return;
    }

    const uploadedFiles = Array.isArray(files) ? files : [];
    const pendingFileIds: string[] = [];
    const completedFileIds: string[] = [];
    uploadedFiles.forEach((file) => {
      const attachment = uploadResultToConversationAttachment(file);
      if (attachment) {
        addConversationAttachment(attachment);
        completedFileIds.push(file.fileId);
        return;
      }

      if (file.status === 'parsing' || file.status === 'uploading' || file.status === 'pending') {
        pendingFileIds.push(file.fileId);
        return;
      }

      completedFileIds.push(file.fileId);
    });

    if (pendingFileIds.length === 0 && completedFileIds.length === 0) {
      return;
    }

    setPendingAutoAttachState((current) => {
      const completedSet = new Set(completedFileIds);
      const currentFileIds = current.chatId === chatId
        ? current.fileIds.filter((fileId) => !completedSet.has(fileId))
        : [];
      const nextFileIds = [...currentFileIds];
      pendingFileIds.forEach((fileId) => {
        if (!nextFileIds.includes(fileId)) {
          nextFileIds.push(fileId);
        }
      });
      return { chatId, fileIds: nextFileIds };
    });
  }, [addConversationAttachment, chatId, refreshConversationFiles]);

  const pendingAutoAttachFileIds = useMemo(
    () => pendingAutoAttachState.chatId === chatId ? pendingAutoAttachState.fileIds : [],
    [chatId, pendingAutoAttachState]
  );

  useEffect(() => {
    if (pendingAutoAttachFileIds.length === 0) {
      return;
    }

    const remainingFileIds = new Set(pendingAutoAttachFileIds);
    conversationFiles.forEach((file) => {
      if (!remainingFileIds.has(file.id)) {
        return;
      }

      const attachment = tryConversationFileToComposerAttachment(file);
      if (!attachment) {
        if (file.status === 'error') {
          remainingFileIds.delete(file.id);
        }
        return;
      }

      addConversationAttachment({ ...attachment, removeBehavior: 'delete' });
      remainingFileIds.delete(file.id);
    });

    if (remainingFileIds.size === pendingAutoAttachFileIds.length) {
      return;
    }

    setPendingAutoAttachState({
      chatId,
      fileIds: Array.from(remainingFileIds),
    });
  }, [addConversationAttachment, chatId, conversationFiles, pendingAutoAttachFileIds]);

  const selectedConversationFileIds = useMemo(
    () => new Set(conversationAttachments.map((file) => file.fileId)),
    [conversationAttachments]
  );


  const lastReadyConversation = lastReadyConversationSnapshot;
  const shouldKeepPreviousContent =
    hydrationView === 'loading' &&
    lastReadyConversation?.chatId === chatId &&
    Boolean(lastReadyConversation && lastReadyConversation.messages.length > 0);
  const displayMessages = shouldKeepPreviousContent
    ? lastReadyConversation?.messages || []
    : conversation?.messages || [];
  const displayConversationId = shouldKeepPreviousContent
    ? lastReadyConversation?.chatId || null
    : chatId;
  const isHydratingWithoutContent = hydrationView === 'loading' && !shouldKeepPreviousContent;
  const visibleStopOutcomeNotice = stopOutcomeNotice?.conversationId === chatId
    && stopOutcomeNotice.authIdentity === authSessionKey ? stopOutcomeNotice : null;
  // 保留上一屏内容时展示的是另一个会话，要问的是"那个会话在不在生成"，
  // 而不是当前路由会话；槽位按会话索引后直接查它自己那一条。
  const isDisplayConversationStreamingSlot = useAppSelector(
    (state) => selectStreamSlot(state, displayConversationId).isStreaming
  );
  const isDisplayConversationStreaming =
    !isHydratingWithoutContent && isDisplayConversationStreamingSlot;

  const handleSurfaceChange = useCallback((surface: string) => {
    if (surface !== 'chat' && surface !== 'trajectory') return;
    dispatch(setTrajectoryActiveSurface({ conversationId: chatId, surface }));
  }, [chatId, dispatch]);

  const handleInspectTrajectory = useCallback((messageId: string, runId: string) => {
    trajectoryInspectSequenceRef.current += 1;
    dispatch(requestTrajectoryInspect({
      conversationId: chatId,
      requestId: `${chatId}:${runId}:${trajectoryInspectSequenceRef.current}`,
      messageId,
      runId,
      spanId: null,
    }));
  }, [chatId, dispatch]);

  const handleRevealInChat = useCallback((messageId: string) => {
    dispatch(setTrajectoryActiveSurface({ conversationId: chatId, surface: 'chat' }));
    window.requestAnimationFrame(() => {
      const target = document.getElementById(getChatMessageDomId(messageId));
      if (!target) return;
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.focus({ preventScroll: true });
    });
  }, [chatId, dispatch]);

  if ((!conversation && !shouldKeepPreviousContent && !isHydratingWithoutContent) || hydrationView === 'error') {
    return (
      <div className="h-full flex items-center justify-center">
          <div className="text-center space-y-4">
            <div className="text-red-500 text-2xl">⚠️</div>
            <p className="text-muted-foreground">{hydrationError || conversationError || '对话不存在或已被删除'}</p>
            <div className="flex items-center justify-center gap-3">
              {hydrationView === 'error' ? (
                <button
                  onClick={retryHydration}
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                >
                  重试加载
                </button>
              ) : null}
              <button
                onClick={() => router.push(CHAT_NEW_PATH)}
                className="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
              >
                返回首页
              </button>
            </div>
          </div>
      </div>
    );
  }

  return (
    <ChatDetailOverlayProvider>
      <div className="relative flex h-full min-h-0">
        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <Tabs
            value={activeSurface}
            onValueChange={handleSurfaceChange}
            activationMode="manual"
            className="min-h-0 flex-1 gap-0"
          >
            <div className="flex shrink-0 justify-center border-b border-border/60 px-4 py-1">
              <TabsList aria-label="会话视图">
                <TabsTrigger value="chat">聊天</TabsTrigger>
                <TabsTrigger value="trajectory">轨迹</TabsTrigger>
              </TabsList>
            </div>
            {visibleStopOutcomeNotice ? (
              <div role="status" className="mx-4 mt-3 flex items-start justify-between gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-2 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
                <span>
                  {visibleStopOutcomeNotice.terminalStatus
                    ? `${STOP_OUTCOME_LABELS[visibleStopOutcomeNotice.terminalStatus]}；先前停止请求未得到确认。`
                    : '停止结果仍未确认，后台可能仍在运行。请稍后查看轨迹核实。'}
                </span>
                <button
                  type="button"
                  className="shrink-0 underline underline-offset-2"
                  onClick={() => {
                    clearStopOutcomeNotice(chatId, visibleStopOutcomeNotice.runId);
                    setStopOutcomeNotice(null);
                  }}
                >
                  关闭提示
                </button>
              </div>
            ) : null}

            <TabsContent
              value="chat"
              forceMount
              hidden={activeSurface !== 'chat'}
              className="min-h-0 data-[state=inactive]:hidden"
            >
              <div className="flex h-full min-h-0 flex-col">
                <LocationContextBanner conversationId={chatId} />
                <div className="flex-1 overflow-y-auto px-4 pt-4" data-chat-scroll-container="true">
                  <ChatMessageListLazy
                    messages={isHydratingWithoutContent ? [] : displayMessages}
                    conversationId={displayConversationId}
                    isStreaming={isDisplayConversationStreaming}
                    loadingState={isHydratingWithoutContent ? 'history-hydration' : undefined}
                    onRetry={shouldKeepPreviousContent || isHydratingWithoutContent ? undefined : handleRetry}
                    onInspectTrajectory={
                      shouldKeepPreviousContent || isHydratingWithoutContent
                        ? undefined
                        : handleInspectTrajectory
                    }
                    suggestedQuestions={shouldKeepPreviousContent || isHydratingWithoutContent ? [] : suggestedQuestions}
                    isLoadingQuestions={shouldKeepPreviousContent || isHydratingWithoutContent ? false : isLoadingQuestions}
                    onSelectQuestion={shouldKeepPreviousContent || isHydratingWithoutContent ? undefined : handleSelectQuestion}
                    onRefreshQuestions={shouldKeepPreviousContent || isHydratingWithoutContent ? undefined : handleRefreshQuestions}
                    completionStateVisible={shouldKeepPreviousContent || isHydratingWithoutContent ? false : showCompletionState}
                    emptyState={CHAT_EMPTY_STATE}
                  />
                </div>
              </div>
            </TabsContent>

            <TabsContent
              value="trajectory"
              forceMount
              hidden={activeSurface !== 'trajectory'}
              className="min-h-0 data-[state=inactive]:hidden"
            >
              <TrajectoryTabView
                conversationId={chatId}
                messages={isHydratingWithoutContent ? [] : displayMessages}
                visible={activeSurface === 'trajectory'}
                contentBottomInset={trajectoryComposerInset}
                onRevealInChat={handleRevealInChat}
              />
            </TabsContent>
          </Tabs>

          <div
            ref={chatInputRef}
            tabIndex={-1}
            data-testid="chat-composer-shell"
            data-placement={activeSurface === 'trajectory' ? 'overlay' : 'flow'}
            className={activeSurface === 'trajectory'
              ? 'pointer-events-none absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-background via-background/95 to-transparent px-4 pb-3 pt-8'
              : 'flex-shrink-0 px-4 pb-4 pt-2'}
          >
            <div
              data-testid="chat-composer-interactive"
              className={activeSurface === 'trajectory'
              ? 'pointer-events-auto rounded-xl bg-background/95 shadow-[0_-8px_30px_rgba(0,0,0,0.08)] backdrop-blur-sm'
              : undefined}
            >
              {shouldShowFilesPanelButton ? (
                <div className="mb-2 flex items-center justify-end">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-1.5"
                    aria-label={filesPanelOpen ? '关闭会话资料' : '打开会话资料'}
                    aria-expanded={filesPanelOpen}
                    onClick={() => setFilesPanelConversationId((current) => current === chatId ? null : chatId)}
                  >
                    <Files className="h-4 w-4" aria-hidden="true" />
                    资料
                  </Button>
                </div>
              ) : null}
              <ChatInput
                onSendMessage={handleSendMessage}
                onClearMessage={handleClearChat}
                onStopStreaming={handleStopStreaming}
                onModelChange={clearQuestions}
                activeChatId={chatId}
                showContextStatus={activeSurface === 'chat'}
                disabled={isHydratingWithoutContent}
                resetSignal={chatId}
                conversationAttachments={conversationAttachments}
                onRemoveConversationAttachment={handleRemoveConversationAttachment}
                onClearConversationAttachments={handleClearConversationAttachments}
                onUploadComplete={handleUploadComplete}
                initialKnowledgeBaseIds={conversation?.knowledge_base_ids}
                onKnowledgeBaseIdsChange={handleComposerKnowledgeBaseIdsChange}
                onKnowledgeSelectionStatusChange={handleComposerKnowledgeSelectionStatusChange}
              />
            </div>
          </div>
        </div>

        {filesPanelOpen ? (
          <div className="absolute inset-y-0 right-0 z-20 w-full max-w-sm bg-background shadow-lg md:relative md:z-auto md:w-80 md:shrink-0 md:shadow-none">
            <ConversationFilesPanel
              open={filesPanelOpen}
              files={conversationFiles}
              isLoading={conversationFilesLoading}
              error={conversationFilesError}
              selectedFileIds={selectedConversationFileIds}
              onClose={() => setFilesPanelConversationId(null)}
              onRefresh={refreshConversationFiles}
              onAddFile={handleAddConversationFile}
              onDeleteFile={handleDeleteConversationFile}
            />
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        isOpen={confirmDialogOpen}
        onClose={() => setConfirmDialogOpen(false)}
        onConfirm={confirmClearChat}
        title="确认清空聊天"
        description="您确定要清空当前聊天内容吗？此操作不可恢复。"
        confirmLabel="删除"
        cancelLabel="取消"
        variant="destructive"
      />
    </ChatDetailOverlayProvider>
  );
}
