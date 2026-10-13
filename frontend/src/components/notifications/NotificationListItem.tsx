'use client';

import { useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, CheckCircle2, Info, ScrollText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { buildChatConversationPath } from '@/lib/routes/chatRoutes';
import { cn } from '@/lib/utils';
import type { NotificationItem } from '@/lib/api/notifications';
import { useAppDispatch } from '@/redux/hooks';
import { openChangelogDialog } from '@/redux/slices/settingsSlice';

/** 打开通知指向的结果：更新日志弹窗，或跳到对应对话并定位回答。 */
export function useOpenNotification() {
  const router = useRouter();
  const dispatch = useAppDispatch();
  const navigationSequenceRef = useRef(0);
  return useCallback((item: NotificationItem) => {
    if (item.target.type === 'changelog') {
      dispatch(openChangelogDialog({ changelogId: item.target.changelog_id }));
      return;
    }
    // 同一记录再次点击也要重新定位，不能被相同 URL 的展示状态吞掉。
    navigationSequenceRef.current += 1;
    const requestId = `${Date.now()}-${navigationSequenceRef.current}`;
    router.push(`${buildChatConversationPath(item.target.conversation_id)}?message=${encodeURIComponent(item.target.message_id)}&run=${encodeURIComponent(item.target.run_id)}&notification=${requestId}`);
  }, [dispatch, router]);
}

export default function NotificationListItem({ item, onSelect }: {
  item: NotificationItem;
  onSelect: (item: NotificationItem) => void;
}) {
  const { t, i18n } = useTranslation();
  const Icon = item.target.type === 'changelog' ? ScrollText : item.kind === 'run_completed' ? CheckCircle2 : item.kind === 'run_failed' ? AlertCircle : Info;
  const date = new Date(item.created_at);
  const dateLabel = Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(i18n.language, {
    timeZone: 'Asia/Shanghai', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
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
