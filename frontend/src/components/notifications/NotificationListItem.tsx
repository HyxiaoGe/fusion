'use client';

import { useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, CheckCircle2, Info, ScrollText, Ticket } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { buildChatConversationPath } from '@/lib/routes/chatRoutes';
import { cn } from '@/lib/utils';
import { markNotificationsRead, type NotificationItem } from '@/lib/api/notifications';
import { notifyNotificationsChanged } from '@/lib/notifications/events';
import { useAppDispatch } from '@/redux/hooks';
import { openChangelogDialog, openCreditsPanel } from '@/redux/slices/settingsSlice';
import { useNotifications } from './NotificationsProvider';
import { track } from '@/lib/analytics/analytics';

/** 打开通知指向的结果：更新日志弹窗、额度面板，或跳到对应对话并定位回答。 */
export function useOpenNotification() {
  const router = useRouter();
  const dispatch = useAppDispatch();
  const { sessionKey } = useNotifications();
  const navigationSequenceRef = useRef(0);
  return useCallback((item: NotificationItem) => {
    track('notification_clicked', { kind: item.kind, target_type: item.target.type });
    if (item.target.type === 'changelog') {
      dispatch(openChangelogDialog({ changelogId: item.target.changelog_id }));
      return;
    }
    if (item.target.type === 'credits') {
      // 额度通知的内容就是标题和摘要本身，点开即已读；面板里可以直接用卡。
      dispatch(openCreditsPanel());
      if (!item.read_at) {
        markNotificationsRead([item.id]).then(() => notifyNotificationsChanged(sessionKey ?? undefined), () => {});
      }
      return;
    }
    // 同一记录再次点击也要重新定位，不能被相同 URL 的展示状态吞掉。
    navigationSequenceRef.current += 1;
    const requestId = `${Date.now()}-${navigationSequenceRef.current}`;
    router.push(`${buildChatConversationPath(item.target.conversation_id)}?message=${encodeURIComponent(item.target.message_id)}&run=${encodeURIComponent(item.target.run_id)}&notification=${requestId}`);
  }, [dispatch, router, sessionKey]);
}

export default function NotificationListItem({ item, onSelect, compact = false }: {
  item: NotificationItem;
  onSelect: (item: NotificationItem) => void;
  /** 消息盒子里一条一行：标题与摘要在左，类型、时间、已读状态在右。 */
  compact?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const Icon = item.target.type === 'changelog' ? ScrollText : item.target.type === 'credits' ? Ticket : item.kind === 'run_completed' ? CheckCircle2 : item.kind === 'run_failed' ? AlertCircle : Info;
  const date = new Date(item.created_at);
  const dateLabel = Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(i18n.language, {
    timeZone: 'Asia/Shanghai', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
  if (compact) {
    return (
      <li>
        <button type="button" data-sidebar-navigation onClick={() => onSelect(item)}
          className={cn('flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', !item.read_at && 'bg-primary/5')}>
          <Icon className={cn('h-4 w-4 shrink-0', item.read_at ? 'text-muted-foreground' : 'text-primary')} aria-hidden="true" />
          <span className="flex min-w-0 flex-1 items-baseline gap-2">
            <span className={cn('shrink-0 truncate text-sm sm:max-w-[45%]', !item.read_at && 'font-semibold')}>{item.title}</span>
            {item.body && <span className="hidden min-w-0 truncate text-xs text-muted-foreground sm:block">{item.body}</span>}
          </span>
          <span className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
            <span className="hidden md:inline">{t(`notifications.businessTypes.${item.business_type ?? 'ai_conversation'}`)}</span>
            <time dateTime={item.created_at} className="tabular-nums">{dateLabel}</time>
            <span>{t(item.read_at ? 'notifications.read' : 'notifications.notRead')}</span>
          </span>
        </button>
      </li>
    );
  }
  return (
    <li>
      <button type="button" data-sidebar-navigation onClick={() => onSelect(item)}
        className={cn('flex w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', !item.read_at && 'bg-primary/5')}>
        <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', item.read_at ? 'text-muted-foreground' : 'text-primary')} aria-hidden="true" />
        <span className="min-w-0 flex-1">
          <span className={cn('block break-words text-sm', !item.read_at && 'font-semibold')}>{item.title}</span>
          {item.body && <span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">{item.body}</span>}
          <span className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span>{t(`notifications.businessTypes.${item.business_type ?? 'ai_conversation'}`)}</span>
            <time dateTime={item.created_at}>{dateLabel}</time>
            <span>{t(item.read_at ? 'notifications.read' : 'notifications.notRead')}</span>
          </span>
        </span>
      </button>
    </li>
  );
}
