import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { syncConversationActivity } from '@/lib/api/sync';
import { registerBackgroundTask } from '@/lib/scheduler/backgroundScheduler';

/** 有对话在生成时的轮询间隔；都空闲时只在聚焦、切回标签页、本页流结束时拉取。 */
export const ACTIVITY_POLL_INTERVAL_MS = 15_000;

const EMPTY_IDS: readonly string[] = [];

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

/**
 * 侧栏的对话活动状态：合并服务端登记的“进行中 / 完成未读”与本页 Redux 里的流。
 *
 * 运行独立于页面连接，刷新或换标签页后本页 Redux 不再知道哪些对话还在跑，
 * 所以以服务端为准；本页刚发起的流先用 Redux 补上，避免等一个轮询周期。
 * 未读由通知中心管理，路由切换本身不再表示结果已读。未登录时不请求。
 * 对话离开“进行中”时回调 onConversationsSettled：转到后台的运行收不到标题等
 * 流内事件，由调用方据此刷新列表元数据。
 */
export function useConversationActivity(
  sessionKey: string | null | undefined,
  _activeChatId: string | null | undefined,
  localStreamingIds: readonly string[],
  onConversationsSettled?: (conversationIds: string[]) => void,
) {
  const [remoteStreamingIds, setRemoteStreamingIds] = useState<readonly string[]>(EMPTY_IDS);
  const [unreadIds, setUnreadIds] = useState<readonly string[]>(EMPTY_IDS);
  const inflightRef = useRef<AbortController | null>(null);
  // 本页刚收尾的对话：界面在 run_completed 就收尾，服务端要等推荐问题发完才撤下“进行中”，
  // 这段时间拉到的旧状态不采纳；服务端不再返回它，或本页又开始生成时移出。
  const locallyEndedRef = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    if (!sessionKey) return;
    inflightRef.current?.abort();
    const controller = new AbortController();
    inflightRef.current = controller;
    try {
      const activity = await syncConversationActivity(controller.signal);
      if (controller.signal.aborted) return;
      const locallyEnded = locallyEndedRef.current;
      for (const id of locallyEnded) {
        if (!activity.streaming.includes(id)) locallyEnded.delete(id);
      }
      const streaming = activity.streaming.filter((id) => !locallyEnded.has(id));
      setRemoteStreamingIds((prev) => (sameIds(prev, streaming) ? prev : streaming));
      setUnreadIds((prev) => (sameIds(prev, activity.unread) ? prev : activity.unread));
    } catch {
      // 状态只是提示，拉取失败保持上一次结果，下次触发再试。
    } finally {
      if (inflightRef.current === controller) inflightRef.current = null;
    }
  }, [sessionKey]);

  // 有对话在生成时按共享节拍轮询；都空闲时节拍跳过，只在登记、回到前台和本页流起止时拉取。
  const hasActiveStreamsRef = useRef(false);
  useEffect(() => {
    setRemoteStreamingIds(EMPTY_IDS);
    setUnreadIds(EMPTY_IDS);
    locallyEndedRef.current.clear();
    if (!sessionKey) return;
    const unregister = registerBackgroundTask((trigger) => {
      if (trigger === 'tick' && !hasActiveStreamsRef.current) return;
      void refresh();
    }, { intervalMs: ACTIVITY_POLL_INTERVAL_MS });
    return () => {
      unregister();
      inflightRef.current?.abort();
    };
  }, [refresh, sessionKey]);

  // 本页的流结束时服务端刚写入未读，立即拉一次。
  const previousLocalRef = useRef<readonly string[]>(localStreamingIds);
  useEffect(() => {
    const endedIds = previousLocalRef.current.filter((id) => !localStreamingIds.includes(id));
    const started = localStreamingIds.some((id) => !previousLocalRef.current.includes(id));
    previousLocalRef.current = localStreamingIds;
    const locallyEnded = locallyEndedRef.current;
    for (const id of localStreamingIds) locallyEnded.delete(id);
    if (endedIds.length > 0) {
      for (const id of endedIds) locallyEnded.add(id);
      setRemoteStreamingIds((prev) => {
        const next = prev.filter((id) => !locallyEnded.has(id));
        return next.length === prev.length ? prev : next;
      });
    }
    if (endedIds.length > 0 || started) void refresh();
  }, [localStreamingIds, refresh]);

  hasActiveStreamsRef.current = remoteStreamingIds.length > 0 || localStreamingIds.length > 0;

  const streamingConversationIds = useMemo(() => {
    if (remoteStreamingIds.length === 0) return localStreamingIds;
    const merged = new Set([...localStreamingIds, ...remoteStreamingIds]);
    return Array.from(merged);
  }, [localStreamingIds, remoteStreamingIds]);

  const settledCallbackRef = useRef(onConversationsSettled);
  settledCallbackRef.current = onConversationsSettled;
  const previousStreamingRef = useRef({ sessionKey, ids: streamingConversationIds });
  useEffect(() => {
    const previous = previousStreamingRef.current;
    previousStreamingRef.current = { sessionKey, ids: streamingConversationIds };
    // 换账号或登出时集合被整体清空，不是运行结束。
    if (previous.sessionKey !== sessionKey) return;
    const settled = previous.ids.filter((id) => !streamingConversationIds.includes(id));
    if (settled.length > 0) settledCallbackRef.current?.(settled);
  }, [sessionKey, streamingConversationIds]);

  const unreadConversationIds = useMemo(
    () => unreadIds,
    [unreadIds],
  );

  return { streamingConversationIds, unreadConversationIds, refresh };
}
