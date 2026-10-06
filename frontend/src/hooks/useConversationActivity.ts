import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getConversationActivity, markConversationRead } from '@/lib/api/chat';

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
 * 打开某个对话即视为已读。未登录（sessionKey 为空）时不请求，换账号时清空。
 */
export function useConversationActivity(
  sessionKey: string | null | undefined,
  activeChatId: string | null | undefined,
  localStreamingIds: readonly string[],
) {
  const [remoteStreamingIds, setRemoteStreamingIds] = useState<readonly string[]>(EMPTY_IDS);
  const [unreadIds, setUnreadIds] = useState<readonly string[]>(EMPTY_IDS);
  const inflightRef = useRef<AbortController | null>(null);
  const readRequestedRef = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    if (!sessionKey) return;
    inflightRef.current?.abort();
    const controller = new AbortController();
    inflightRef.current = controller;
    try {
      const activity = await getConversationActivity(controller.signal);
      if (controller.signal.aborted) return;
      setRemoteStreamingIds((prev) => (sameIds(prev, activity.streaming) ? prev : activity.streaming));
      setUnreadIds((prev) => (sameIds(prev, activity.unread) ? prev : activity.unread));
      // 服务端已不再标未读的对话，允许以后再次请求已读。
      for (const id of readRequestedRef.current) {
        if (!activity.unread.includes(id)) readRequestedRef.current.delete(id);
      }
    } catch {
      // 状态只是提示，拉取失败保持上一次结果，下次触发再试。
    } finally {
      if (inflightRef.current === controller) inflightRef.current = null;
    }
  }, [sessionKey]);

  useEffect(() => {
    setRemoteStreamingIds(EMPTY_IDS);
    setUnreadIds(EMPTY_IDS);
    readRequestedRef.current.clear();
    if (!sessionKey) return;
    void refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    window.addEventListener('focus', onVisible);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onVisible);
      document.removeEventListener('visibilitychange', onVisible);
      inflightRef.current?.abort();
    };
  }, [refresh, sessionKey]);

  // 本页的流结束时服务端刚写入未读，立即拉一次。
  const previousLocalRef = useRef<readonly string[]>(localStreamingIds);
  useEffect(() => {
    const ended = previousLocalRef.current.some((id) => !localStreamingIds.includes(id));
    const started = localStreamingIds.some((id) => !previousLocalRef.current.includes(id));
    previousLocalRef.current = localStreamingIds;
    if (ended || started) void refresh();
  }, [localStreamingIds, refresh]);

  const hasActiveStreams = remoteStreamingIds.length > 0 || localStreamingIds.length > 0;
  useEffect(() => {
    if (!hasActiveStreams) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, ACTIVITY_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [hasActiveStreams, refresh]);

  useEffect(() => {
    if (!activeChatId || !unreadIds.includes(activeChatId)) return;
    setUnreadIds((prev) => prev.filter((id) => id !== activeChatId));
    if (readRequestedRef.current.has(activeChatId)) return;
    readRequestedRef.current.add(activeChatId);
    markConversationRead(activeChatId).catch(() => {
      readRequestedRef.current.delete(activeChatId);
    });
  }, [activeChatId, unreadIds]);

  const streamingConversationIds = useMemo(() => {
    if (remoteStreamingIds.length === 0) return localStreamingIds;
    const merged = new Set([...localStreamingIds, ...remoteStreamingIds]);
    return Array.from(merged);
  }, [localStreamingIds, remoteStreamingIds]);

  const unreadConversationIds = useMemo(
    () => unreadIds.filter((id) => id !== activeChatId && !streamingConversationIds.includes(id)),
    [activeChatId, streamingConversationIds, unreadIds],
  );

  return { streamingConversationIds, unreadConversationIds, refresh };
}
