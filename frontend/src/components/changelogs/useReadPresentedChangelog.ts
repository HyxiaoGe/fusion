'use client';

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import { markNotificationsRead } from '@/lib/api/notifications';
import { notifyNotificationsChanged } from '@/lib/notifications/events';

interface PresentedChangelog {
  sessionKey: string | null;
  changelogId: string;
  notificationId: string | null;
  bodyRef: RefObject<HTMLDivElement | null>;
}

/** 日志正文已经渲染并进入可见页面视口后才确认已读，访问接口或点击跳转不能代替展示。 */
export function useReadPresentedChangelog({ sessionKey, changelogId, notificationId, bodyRef }: PresentedChangelog): {
  readError: boolean;
  retryRead: () => void;
} {
  const scope = JSON.stringify([sessionKey, changelogId, notificationId]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const acknowledgedRef = useRef({ scope: '', acknowledged: false });
  const retryRef = useRef<() => void>(() => {});
  const [errorScope, setErrorScope] = useState<string | null>(null);
  const retryRead = useCallback(() => { retryRef.current(); }, []);

  useEffect(() => {
    const body = bodyRef.current;
    if (!sessionKey || !changelogId || !notificationId || !body || typeof IntersectionObserver === 'undefined') return;
    if (acknowledgedRef.current.scope !== scope) acknowledgedRef.current = { scope, acknowledged: false };
    const acknowledged = acknowledgedRef.current;
    const controller = new AbortController();
    let intersecting = false;
    let pending = false;
    const isCurrent = () => scopeRef.current === scope && !controller.signal.aborted;
    const confirmRead = async () => {
      if (!isCurrent() || acknowledged.acknowledged || pending || !intersecting
        || document.visibilityState !== 'visible' || !body.isConnected || body.closest('[hidden]')) return;
      pending = true;
      setErrorScope(null);
      try {
        await markNotificationsRead([notificationId], controller.signal);
        if (!isCurrent()) return;
        acknowledged.acknowledged = true;
        notifyNotificationsChanged(sessionKey);
      } catch {
        // 失败时不清未读；正文仍可见时由用户重试或恢复页面再次确认。
        if (isCurrent()) setErrorScope(scope);
      } finally {
        pending = false;
      }
    };
    const retry = () => { void confirmRead(); };
    retryRef.current = retry;
    const observer = new IntersectionObserver((entries) => {
      if (!isCurrent()) return;
      for (const entry of entries) {
        if (entry.target === body) intersecting = entry.isIntersecting;
      }
      retry();
    }, { threshold: 0.01 });
    observer.observe(body);
    window.addEventListener('focus', retry);
    document.addEventListener('visibilitychange', retry);
    return () => {
      controller.abort();
      observer.disconnect();
      window.removeEventListener('focus', retry);
      document.removeEventListener('visibilitychange', retry);
      if (retryRef.current === retry) retryRef.current = () => {};
    };
  }, [bodyRef, changelogId, notificationId, scope, sessionKey]);

  return { readError: errorScope === scope, retryRead };
}
