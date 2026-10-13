'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { Bell, Loader2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { useHasMounted } from '@/hooks/useHasMounted';
import type { NotificationItem } from '@/lib/api/notifications';
import { useAppDispatch } from '@/redux/hooks';
import { openNotificationsDialog } from '@/redux/slices/settingsSlice';
import NotificationListItem, { useOpenNotification } from './NotificationListItem';
import { useNotifications } from './NotificationsProvider';

export default function NotificationCenter() {
  const hasMounted = useHasMounted();
  const notifications = useNotifications();
  const dispatch = useAppDispatch();
  const openNotification = useOpenNotification();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const navigatingRef = useRef(false);
  const titleId = useId();

  useEffect(() => {
    setOpen(false);
    navigatingRef.current = false;
  }, [notifications.sessionKey]);

  // 认证会从浏览器存储同步恢复；服务端与客户端首帧都先隐藏入口，挂载后再显示。
  if (!hasMounted || !notifications.sessionKey) return null;

  const onOpenChange = (nextOpen: boolean) => {
    navigatingRef.current = false;
    setOpen(nextOpen);
    if (nextOpen) notifications.ensureFresh();
  };
  const selectNotification = (item: NotificationItem) => {
    // 跳转只关闭面板；对应结果成功展示后的已读回执由聊天页负责。
    navigatingRef.current = true;
    setOpen(false);
    openNotification(item);
  };
  const viewAll = () => {
    navigatingRef.current = true;
    setOpen(false);
    dispatch(openNotificationsDialog());
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
              {notifications.items.map((item) => <NotificationListItem key={item.id} item={item} onSelect={selectNotification} />)}
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
        <div className="flex shrink-0 justify-center border-t px-4 py-2">
          <Button type="button" variant="ghost" size="sm" className="text-xs" onClick={viewAll}>
            {t('notifications.viewAll')}
          </Button>
        </div>
        <span role="status" aria-live="polite" className="sr-only">{label}</span>
      </PopoverContent>
    </Popover>
  );
}
