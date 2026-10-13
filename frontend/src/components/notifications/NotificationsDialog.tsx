'use client';

import { useCallback, useEffect, useState } from 'react';
import { Bell, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { getNotifications, type NotificationFilter, type NotificationItem } from '@/lib/api/notifications';
import { useAppDispatch, useAppSelector } from '@/redux/hooks';
import { closeNotificationsDialog } from '@/redux/slices/settingsSlice';
import NotificationListItem, { useOpenNotification } from './NotificationListItem';
import { useNotifications } from './NotificationsProvider';

// 横向窗口一页放得下，不用在页内再滚动。
const PAGE_SIZE = 10;

interface PageState {
  items: NotificationItem[];
  nextCursor: string | null;
  loading: boolean;
  error: boolean;
}

/** 消息盒子：与更新日志一样在当前页面弹出，按页查看全部通知。 */
export default function NotificationsDialog() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const notifications = useNotifications();
  const openNotification = useOpenNotification();
  const open = useAppSelector((state) => state.settings.isNotificationsDialogOpen);
  const [filter, setFilter] = useState<NotificationFilter>('all');
  // 第 N 页的游标；第一页没有游标。
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<PageState>({ items: [], nextCursor: null, loading: false, error: false });
  const [reloadKey, setReloadKey] = useState(0);
  const cursor = cursors[pageIndex] ?? null;
  const { sessionKey, revision } = notifications;

  const resetPaging = useCallback(() => {
    setCursors([null]);
    setPageIndex(0);
  }, []);

  useEffect(() => {
    if (!open) resetPaging();
  }, [open, resetPaging]);

  useEffect(() => {
    // 换账号时不展示上一个账号的通知。
    setPage({ items: [], nextCursor: null, loading: false, error: false });
    resetPaging();
  }, [sessionKey, resetPaging]);

  // 打开、换页、换筛选，或通知有变化（新通知、已读、清理）时重新取当前页。
  useEffect(() => {
    if (!open || !sessionKey) return;
    const controller = new AbortController();
    setPage((current) => ({ ...current, loading: true, error: false }));
    getNotifications({ filter, cursor: cursor ?? undefined, limit: PAGE_SIZE }, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (!result.items.length && pageIndex > 0) {
          // 已读或清理后这一页空了，回到第一页。
          resetPaging();
          return;
        }
        setPage({ items: result.items, nextCursor: result.next_cursor, loading: false, error: false });
      })
      .catch(() => {
        if (!controller.signal.aborted) setPage((current) => ({ ...current, loading: false, error: true }));
      });
    return () => controller.abort();
  }, [open, sessionKey, filter, cursor, pageIndex, revision, reloadKey, resetPaging]);

  const close = () => dispatch(closeNotificationsDialog());
  const selectFilter = (next: NotificationFilter) => {
    if (next === filter) return;
    setFilter(next);
    resetPaging();
  };
  const goNext = () => {
    if (!page.nextCursor || page.loading) return;
    const nextCursor = page.nextCursor;
    setCursors((current) => [...current.slice(0, pageIndex + 1), nextCursor]);
    setPageIndex(pageIndex + 1);
  };
  const goPrevious = () => {
    if (pageIndex === 0 || page.loading) return;
    setPageIndex(pageIndex - 1);
  };
  const selectNotification = (item: NotificationItem) => {
    // 跳转只关闭窗口；对应结果成功展示后的已读回执由聊天页负责。
    close();
    openNotification(item);
  };
  const firstLoading = page.loading && !page.items.length && !page.error;

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) close(); }}>
      <DialogContent closeLabel={t('notifications.close')} className="flex h-[min(600px,85vh)] w-full max-w-[95vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="shrink-0 border-b px-6 py-4">
          <DialogTitle className="flex items-center gap-2"><Bell className="h-5 w-5" aria-hidden="true" />{t('notifications.boxTitle')}</DialogTitle>
          <DialogDescription>{t('notifications.boxDescription')}</DialogDescription>
        </DialogHeader>
        <div className="flex shrink-0 items-center justify-between gap-2 border-b px-4 py-2">
          <div className="flex gap-1" role="group" aria-label={t('notifications.filters')}>
            {(['all', 'unread'] as const).map((value) => (
              <Button key={value} type="button" size="sm"
                variant={filter === value ? 'secondary' : 'ghost'}
                aria-pressed={filter === value} onClick={() => selectFilter(value)}>
                {t(`notifications.${value}`)}
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
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          {notifications.readError && <p role="alert" className="px-4 pt-3 text-xs text-destructive">{t('notifications.readFailed')}</p>}
          {page.error && (
            <div role="alert" className="flex items-center justify-between gap-2 px-4 py-3 text-xs text-muted-foreground">
              <span>{t('notifications.loadFailed')}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => setReloadKey((key) => key + 1)}>{t('notifications.retry')}</Button>
            </div>
          )}
          {firstLoading && <p role="status" className="px-4 py-8 text-center text-sm text-muted-foreground">{t('notifications.loading')}</p>}
          {!page.loading && !page.error && !page.items.length && (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {t(filter === 'unread' ? 'notifications.emptyUnread' : 'notifications.empty')}
            </p>
          )}
          {page.items.length > 0 && (
            <ul className="divide-y divide-border/60" aria-busy={page.loading}>
              {page.items.map((item) => <NotificationListItem key={item.id} item={item} onSelect={selectNotification} compact />)}
            </ul>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t px-4 py-2">
          <Button type="button" variant="ghost" size="sm" onClick={goPrevious} disabled={pageIndex === 0 || page.loading}>
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />{t('notifications.previousPage')}
          </Button>
          <span className="text-xs text-muted-foreground" aria-live="polite">{t('notifications.pageNumber', { page: pageIndex + 1 })}</span>
          <Button type="button" variant="ghost" size="sm" onClick={goNext} disabled={!page.nextCursor || page.loading}>
            {t('notifications.nextPage')}<ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
