'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, Bell, CheckCircle2, Info, Loader2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { buildChatConversationPath } from '@/lib/routes/chatRoutes';
import { cn } from '@/lib/utils';
import type { NotificationItem } from '@/lib/api/notifications';
import { useNotifications } from './NotificationsProvider';

export default function NotificationCenter() {
  const notifications = useNotifications();
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const navigatingRef = useRef(false);
  const navigationSequenceRef = useRef(0);
  const titleId = useId();

  useEffect(() => {
    setOpen(false);
    navigatingRef.current = false;
  }, [notifications.sessionKey]);

  if (!notifications.sessionKey) return null;

  const onOpenChange = (nextOpen: boolean) => {
    navigatingRef.current = false;
    setOpen(nextOpen);
    if (nextOpen) notifications.refresh();
  };
  const selectNotification = (item: NotificationItem) => {
    // 跳转只关闭面板；对应结果成功展示后的已读回执由聊天页负责。
    navigatingRef.current = true;
    setOpen(false);
    // 同一记录再次点击也要重新定位，不能被相同 URL 的展示状态吞掉。
    navigationSequenceRef.current += 1;
    const requestId = `${Date.now()}-${navigationSequenceRef.current}`;
    router.push(`${buildChatConversationPath(item.target.conversation_id)}?message=${encodeURIComponent(item.target.message_id)}&run=${encodeURIComponent(item.target.run_id)}&notification=${requestId}`);
  };
  const firstLoading = !notifications.loaded && !notifications.error;
  const label = notifications.unreadCount > 0
    ? t('notifications.triggerUnread', { count: notifications.unreadCount }) : t('notifications.title');

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button type="button" variant="ghost" size="icon" className="relative h-8 w-8 text-muted-foreground"
          aria-label={label} title={label}>
          <Bell className="h-4 w-4" aria-hidden="true" />
          {notifications.unreadCount > 0 && (
            <span aria-hidden="true" data-testid="notification-unread-count"
              className="absolute -right-1 -top-1 min-w-4 rounded-full bg-primary px-1 text-center text-[10px] font-semibold leading-4 text-primary-foreground">
              {notifications.unreadCount > 99 ? '99+' : notifications.unreadCount}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" sideOffset={10} collisionPadding={12}
        aria-labelledby={titleId}
        className="flex w-[min(24rem,calc(100vw-1.5rem))] max-h-[min(32rem,var(--radix-popover-content-available-height))] flex-col overflow-hidden p-0"
        onCloseAutoFocus={(event) => {
          if (navigatingRef.current) event.preventDefault();
        }}>
        <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-2 pt-3">
          <h2 id={titleId} className="text-sm font-semibold">{t('notifications.title')}</h2>
          <Button type="button" variant="ghost" size="icon" className="h-7 w-7"
            aria-label={t('notifications.close')} onClick={() => onOpenChange(false)}>
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 pb-2">
          <div className="flex gap-1" role="group" aria-label={t('notifications.filters')}>
            {(['all', 'unread'] as const).map((filter) => (
              <Button key={filter} type="button" size="sm"
                variant={notifications.filter === filter ? 'secondary' : 'ghost'}
                aria-pressed={notifications.filter === filter} onClick={() => notifications.setFilter(filter)}>
                {t(`notifications.${filter}`)}
              </Button>
            ))}
          </div>
          <Button type="button" variant="ghost" size="sm" className="text-xs"
            disabled={!notifications.loaded || !notifications.unreadCount || notifications.readingAll}
            onClick={() => { void notifications.markAllRead(); }}>
            {notifications.readingAll && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
            {t('notifications.readAll')}
          </Button>
        </div>
        <div className="min-h-0 overflow-y-auto overscroll-contain">
          {notifications.readError && <p role="alert" className="px-4 pt-3 text-xs text-destructive">{t('notifications.readFailed')}</p>}
          {notifications.error && (
            <div role="alert" className="flex items-center justify-between gap-2 px-4 py-3 text-xs text-muted-foreground">
              <span>{t(notifications.loaded ? 'notifications.refreshFailed' : 'notifications.loadFailed')}</span>
              <Button type="button" variant="outline" size="sm" onClick={notifications.refresh}>{t('notifications.retry')}</Button>
            </div>
          )}
          {firstLoading && <p role="status" className="px-4 py-8 text-center text-sm text-muted-foreground">{t('notifications.loading')}</p>}
          {notifications.loaded && !notifications.items.length && (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {t(notifications.filter === 'unread' ? 'notifications.emptyUnread' : 'notifications.empty')}
            </p>
          )}
          {notifications.items.length > 0 && (
            <ul className="divide-y divide-border/60">
              {notifications.items.map((item) => {
                const Icon = item.kind === 'run_completed' ? CheckCircle2 : item.kind === 'run_failed' ? AlertCircle : Info;
                const date = new Date(item.created_at);
                const dateLabel = Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(i18n.language, {
                  timeZone: 'Asia/Shanghai', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                }).format(date);
                return (
                  <li key={item.id}>
                    <button type="button" data-sidebar-navigation onClick={() => selectNotification(item)}
                      className={cn('flex w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', !item.read_at && 'bg-primary/5')}>
                      <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', item.read_at ? 'text-muted-foreground' : 'text-primary')} aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className={cn('block break-words text-sm', !item.read_at && 'font-semibold')}>{item.title}</span>
                        {item.body && <span className="mt-1 block break-words text-xs leading-5 text-muted-foreground">{item.body}</span>}
                        <span className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                          <time dateTime={item.created_at}>{dateLabel}</time>
                          <span>{t(item.read_at ? 'notifications.read' : 'notifications.notRead')}</span>
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {notifications.pageError && <p role="alert" className="px-4 pt-3 text-center text-xs text-muted-foreground">{t('notifications.loadMoreFailed')}</p>}
          {notifications.nextCursor && (
            <div className="flex justify-center px-4 py-3">
              <Button type="button" variant="ghost" size="sm" onClick={notifications.loadMore}
                disabled={notifications.loadingMore || notifications.loading}>
                {notifications.loadingMore ? t('notifications.loading') : notifications.pageError ? t('notifications.retry') : t('notifications.loadMore')}
              </Button>
            </div>
          )}
        </div>
        <span role="status" aria-live="polite" className="sr-only">{label}</span>
      </PopoverContent>
    </Popover>
  );
}
