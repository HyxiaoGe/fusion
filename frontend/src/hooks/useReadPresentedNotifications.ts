'use client';

import { useEffect, useRef, type RefObject } from 'react';
import { markNotificationResultsRead } from '@/lib/api/notifications';
import { notifyNotificationsChanged } from '@/lib/notifications/events';
import { getChatMessageDomId } from '@/lib/chat/messageDom';

export interface PresentedNotificationResult {
  run_id: string;
  message_id: string;
  domMessageId: string;
  domId?: string;
}

/** 只确认已经渲染并进入聊天视口的终态结果；路由、缓存和后台页面不能代替展示。 */
export function useReadPresentedNotifications(
  containerRef: RefObject<HTMLElement | null>,
  sessionKey: string | null | undefined,
  conversationId: string | null | undefined,
  enabled: boolean,
  results: PresentedNotificationResult[],
) {
  const resultsRef = useRef(results);
  resultsRef.current = results;
  const signature = results.map(result => `${result.run_id}:${result.message_id}:${result.domId ?? result.domMessageId}`).join('|');
  const acknowledgedRef = useRef({ scope: '', keys: new Set<string>() });

  useEffect(() => {
    if (!sessionKey || !conversationId || !enabled || !containerRef.current || !signature) return;
    if (typeof IntersectionObserver === 'undefined') return;
    const scope = `${sessionKey}:${conversationId}`;
    if (acknowledgedRef.current.scope !== scope) acknowledgedRef.current = { scope, keys: new Set() };
    const acknowledged = acknowledgedRef.current.keys;
    const controller = new AbortController();
    const visible = new Map<string, PresentedNotificationResult>();
    const pending = new Set<string>();
    const root = containerRef.current.closest<HTMLElement>('[data-chat-scroll-container="true"]');
    let frame: number | null = null;

    const flush = () => {
      frame = null;
      if (controller.signal.aborted || document.visibilityState !== 'visible') return;
      const batch = [...visible.entries()].filter(([key]) => !acknowledged.has(key) && !pending.has(key)).slice(0, 100);
      if (!batch.length) return;
      batch.forEach(([key]) => pending.add(key));
      let succeeded = false;
      void markNotificationResultsRead({
        conversation_id: conversationId,
        results: batch.map(([, result]) => ({ run_id: result.run_id, message_id: result.message_id })),
      }, controller.signal).then(() => {
        if (controller.signal.aborted) return;
        batch.forEach(([key]) => acknowledged.add(key));
        succeeded = true;
        notifyNotificationsChanged(sessionKey);
      }).catch(() => {
        // 不提前清角标；暂时失败时在下一次可见轮询重试。
      }).finally(() => {
        batch.forEach(([key]) => pending.delete(key));
        if (!controller.signal.aborted && succeeded && batch.length === 100) schedule();
      });
    };
    const schedule = () => {
      if (frame === null && !controller.signal.aborted) frame = window.requestAnimationFrame(flush);
    };
    const targets = new Map<Element, PresentedNotificationResult>();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const result = targets.get(entry.target);
        if (!result) continue;
        const key = `${result.run_id}:${result.message_id}`;
        if (entry.isIntersecting && !entry.target.closest('[hidden]')) visible.set(key, result);
        else visible.delete(key);
      }
      schedule();
    }, { root, threshold: 0.01 });
    for (const result of resultsRef.current) {
      const element = document.getElementById(result.domId ?? getChatMessageDomId(result.domMessageId));
      if (!element || !containerRef.current.contains(element)) continue;
      targets.set(element, result);
      observer.observe(element);
    }
    const timer = window.setInterval(schedule, 15_000);
    document.addEventListener('visibilitychange', schedule);
    window.addEventListener('focus', schedule);
    return () => {
      controller.abort();
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', schedule);
      window.removeEventListener('focus', schedule);
    };
  }, [containerRef, conversationId, enabled, sessionKey, signature]);
}
