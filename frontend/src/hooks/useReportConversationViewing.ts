'use client';

import { useEffect } from 'react';
import { clearConversationViewing, reportConversationViewing } from '@/lib/api/notifications';

// 后端标记 30 秒过期，每 15 秒续一次。
const REPORT_INTERVAL = 15_000;

/** 对话页可见时告诉后端「正在看」，这个对话结束的回答就不再登记通知。 */
export function useReportConversationViewing(conversationId: string | null | undefined, sessionKey: string | null | undefined) {
  useEffect(() => {
    if (!conversationId || !sessionKey) return;
    let timer: number | null = null;
    let reported = false;
    let controller: AbortController | null = null;

    const report = () => {
      controller?.abort();
      controller = new AbortController();
      reported = true;
      // 上报失败只会多一条通知，不打扰用户。
      void reportConversationViewing(conversationId, controller.signal).catch(() => {});
    };
    const start = () => {
      if (timer !== null) return;
      report();
      timer = window.setInterval(report, REPORT_INTERVAL);
    };
    const stop = () => {
      if (timer !== null) window.clearInterval(timer);
      timer = null;
      controller?.abort();
      controller = null;
      if (!reported) return;
      reported = false;
      void clearConversationViewing(conversationId).catch(() => {});
    };
    const sync = () => {
      if (document.visibilityState === 'visible') start();
      else stop();
    };

    sync();
    document.addEventListener('visibilitychange', sync);
    window.addEventListener('pagehide', stop);
    return () => {
      document.removeEventListener('visibilitychange', sync);
      window.removeEventListener('pagehide', stop);
      stop();
    };
  }, [conversationId, sessionKey]);
}
