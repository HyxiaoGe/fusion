'use client';

import { useEffect } from 'react';
import { clearConversationViewing } from '@/lib/api/notifications';
import { syncConversationViewing } from '@/lib/api/sync';
import { registerBackgroundTask } from '@/lib/scheduler/backgroundScheduler';

// 后端标记 30 秒过期，按共享节拍（15 秒）续期；节拍对齐后最长间隔约 22 秒，仍在过期前。
const REPORT_INTERVAL = 15_000;

/** 对话页可见时告诉后端「正在看」，这个对话结束的回答就不再登记通知。 */
export function useReportConversationViewing(conversationId: string | null | undefined, sessionKey: string | null | undefined) {
  useEffect(() => {
    if (!conversationId || !sessionKey) return;
    let reported = false;
    let controller: AbortController | null = null;

    const report = () => {
      controller?.abort();
      controller = new AbortController();
      reported = true;
      // 与同一节拍的其他后台同步合成一个请求；上报失败只会多一条通知，不打扰用户。
      void syncConversationViewing(conversationId, controller.signal).catch(() => {});
    };
    const clear = () => {
      controller?.abort();
      controller = null;
      if (!reported) return;
      reported = false;
      void clearConversationViewing(conversationId).catch(() => {});
    };

    const unregister = registerBackgroundTask((trigger) => {
      // 仅聚焦（页面一直可见）时标记仍有效，不必重报；从后台切回来时标记已撤，要立即重报。
      if (trigger === 'resume' && reported) return;
      report();
    }, { intervalMs: REPORT_INTERVAL, onHidden: clear });
    return () => {
      unregister();
      clear();
    };
  }, [conversationId, sessionKey]);
}
