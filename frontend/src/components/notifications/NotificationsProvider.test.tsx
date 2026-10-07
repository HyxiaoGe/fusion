import React from 'react';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NotificationItem, NotificationPage, NotificationReadResponse } from '@/lib/api/notifications';

const { getNotificationsMock, markAllNotificationsReadMock } = vi.hoisted(() => ({
  getNotificationsMock: vi.fn(),
  markAllNotificationsReadMock: vi.fn(),
}));

vi.mock('@/lib/api/notifications', () => ({
  getNotifications: getNotificationsMock,
  markAllNotificationsRead: markAllNotificationsReadMock,
}));

import { notifyNotificationsChanged } from '@/lib/notifications/events';
import { NotificationsProvider, useNotifications } from './NotificationsProvider';

let snapshot: ReturnType<typeof useNotifications>;
let visibility: DocumentVisibilityState;
let originalVisibility: PropertyDescriptor | undefined;

function Capture() {
  snapshot = useNotifications();
  return <div>{snapshot.items.map((item) => <span key={item.id}>{item.id}</span>)}</div>;
}

function tree(sessionKey: string | null) {
  return <NotificationsProvider sessionKey={sessionKey}><Capture /></NotificationsProvider>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function notification(id: string, revision: number, read = false): NotificationItem {
  return {
    id,
    business_type: 'ai_conversation',
    kind: 'run_completed',
    title: `任务 ${id}`,
    body: '任务已完成',
    created_at: '2026-10-07T02:00:00Z',
    read_at: read ? '2026-10-07T02:01:00Z' : null,
    created_revision: revision,
    target: { type: 'conversation', conversation_id: `chat-${id}`, message_id: `message-${id}`, run_id: `run-${id}` },
  };
}

function page(items: NotificationItem[], revision = 10, overrides: Partial<NotificationPage> = {}): NotificationPage {
  const unread = items.filter((item) => !item.read_at);
  return {
    items,
    unread_count: unread.length,
    unread_conversation_ids: unread.flatMap((item) => item.target.type === 'conversation' ? [item.target.conversation_id] : []),
    revision,
    next_cursor: null,
    ...overrides,
  };
}

describe('NotificationsProvider', () => {
  beforeEach(() => {
    getNotificationsMock.mockReset();
    markAllNotificationsReadMock.mockReset();
    visibility = 'visible';
    originalVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    if (originalVisibility) Object.defineProperty(document, 'visibilityState', originalVisibility);
    else Reflect.deleteProperty(document, 'visibilityState');
  });

  it('可见页面每 15 秒轮询，隐藏时暂停，恢复焦点后刷新，卸载后释放定时器', async () => {
    vi.useFakeTimers();
    getNotificationsMock.mockResolvedValue(page([notification('first', 1)]));
    const view = render(tree('account-a'));
    await act(async () => {});
    expect(getNotificationsMock).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);

    visibility = 'hidden';
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);

    visibility = 'visible';
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
  });

  it('切换账号会中止旧查询并丢弃迟到响应，退出后立即清空通知', async () => {
    const oldRequest = deferred<NotificationPage>();
    getNotificationsMock.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce(page([notification('new', 2)]));
    const view = render(tree('account-a'));
    const oldSignal = getNotificationsMock.mock.calls[0][1] as AbortSignal;

    view.rerender(tree('account-b'));
    expect(oldSignal.aborted).toBe(true);
    await waitFor(() => { expect(snapshot.items.map((item) => item.id)).toEqual(['new']); });
    await act(async () => { oldRequest.resolve(page([notification('old', 1)], 99)); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['new']);
    expect(snapshot.unreadConversationIds).toEqual(['chat-new']);

    view.rerender(tree(null));
    expect(snapshot.items).toEqual([]);
    expect(snapshot.unreadCount).toBe(0);
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
  });

  it('正文已读事件仅刷新所属账号，并使已中止的旧版本查询失效', async () => {
    const oldRequest = deferred<NotificationPage>();
    getNotificationsMock.mockReturnValueOnce(oldRequest.promise).mockResolvedValueOnce(page([], 12));
    render(tree('account-a'));
    const oldSignal = getNotificationsMock.mock.calls[0][1] as AbortSignal;

    act(() => { notifyNotificationsChanged('account-b'); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(1);
    act(() => { notifyNotificationsChanged('account-a'); });
    expect(oldSignal.aborted).toBe(true);
    await waitFor(() => { expect(snapshot.revision).toBe(12); });
    await act(async () => { oldRequest.resolve(page([notification('already-read', 5)], 10)); });
    expect(snapshot.items).toEqual([]);
    expect(snapshot.unreadCount).toBe(0);
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
  });

  it('全部已读使用操作时的版本截断，保留并发新通知和已读历史', async () => {
    const oldUnread = notification('old', 5);
    const history = notification('history', 8, true);
    const newUnread = notification('new', 11);
    const readRequest = deferred<NotificationReadResponse>();
    const finalRefresh = deferred<NotificationPage>();
    getNotificationsMock
      .mockResolvedValueOnce(page([oldUnread, history], 10))
      .mockResolvedValueOnce(page([newUnread, oldUnread, history], 11))
      .mockReturnValueOnce(finalRefresh.promise);
    markAllNotificationsReadMock.mockReturnValue(readRequest.promise);
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });

    act(() => { void snapshot.markAllRead(); });
    expect(markAllNotificationsReadMock).toHaveBeenCalledWith(10, expect.any(AbortSignal));
    act(() => { snapshot.refresh(); });
    await waitFor(() => { expect(snapshot.revision).toBe(11); });
    await act(async () => {
      readRequest.resolve({ updated_count: 1, unread_count: 1, unread_conversation_ids: ['chat-new'], revision: 12 });
    });

    expect(snapshot.unreadCount).toBe(1);
    expect(snapshot.unreadConversationIds).toEqual(['chat-new']);
    expect(snapshot.items.map((item) => item.id)).toEqual(['new', 'old', 'history']);
    expect(snapshot.items.find((item) => item.id === 'old')?.read_at).not.toBeNull();
    expect(snapshot.items.find((item) => item.id === 'new')?.read_at).toBeNull();
    expect(snapshot.items.find((item) => item.id === 'history')?.read_at).toBe(history.read_at);
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
  });

  it('分页失败保留已有内容，重试后追加去重且未读总数以服务端快照为准', async () => {
    const first = notification('first', 20);
    const second = notification('second', 19);
    const snapshotFields = { unread_count: 5, unread_conversation_ids: ['chat-first', 'chat-second', 'chat-outside-page'] };
    getNotificationsMock
      .mockResolvedValueOnce(page([first], 30, { ...snapshotFields, next_cursor: '20' }))
      .mockRejectedValueOnce(new Error('network failed'))
      .mockResolvedValueOnce(page([first, second], 30, snapshotFields));
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });

    act(() => { snapshot.loadMore(); });
    await waitFor(() => { expect(snapshot.pageError).toBe(true); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['first']);
    expect(snapshot.nextCursor).toBe('20');
    expect(snapshot.unreadCount).toBe(5);
    expect(snapshot.unreadConversationIds).toContain('chat-outside-page');

    act(() => { snapshot.loadMore(); });
    await waitFor(() => { expect(snapshot.items).toHaveLength(2); });
    expect(getNotificationsMock).toHaveBeenLastCalledWith({ filter: 'all', cursor: '20', limit: 20 }, expect.any(AbortSignal));
    expect(snapshot.items.map((item) => item.id)).toEqual(['first', 'second']);
    expect(snapshot.pageError).toBe(false);
    expect(snapshot.nextCursor).toBeNull();
    expect(snapshot.unreadCount).toBe(5);
  });

  it('全部已读和后台刷新失败不会清空历史或误消除未读状态', async () => {
    getNotificationsMock.mockResolvedValueOnce(page([notification('first', 5)], 10)).mockRejectedValueOnce(new Error('network failed'));
    markAllNotificationsReadMock.mockRejectedValueOnce(new Error('read failed'));
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });

    await act(async () => { await snapshot.markAllRead(); });
    expect(snapshot.readError).toBe(true);
    expect(snapshot.readingAll).toBe(false);
    expect(snapshot.unreadCount).toBe(1);
    expect(snapshot.items[0].read_at).toBeNull();

    act(() => { snapshot.refresh(); });
    await waitFor(() => { expect(snapshot.error).toBe(true); });
    expect(snapshot.loaded).toBe(true);
    expect(snapshot.items.map((item) => item.id)).toEqual(['first']);
    expect(snapshot.unreadCount).toBe(1);
    expect(snapshot.unreadConversationIds).toEqual(['chat-first']);
  });

  it('首次访问筛选请求一次，来回切换立即恢复已展开页面和游标', async () => {
    const first = notification('first', 5);
    const second = notification('second', 4, true);
    getNotificationsMock
      .mockResolvedValueOnce(page([first], 10, { next_cursor: 'first-page' }))
      .mockResolvedValueOnce(page([second], 10, { unread_count: 1, next_cursor: 'second-page' }))
      .mockResolvedValueOnce(page([first], 10));
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.loadMore(); });
    await waitFor(() => { expect(snapshot.items).toHaveLength(2); });

    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.filter).toBe('unread'); expect(snapshot.loaded).toBe(true); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
    act(() => { snapshot.setFilter('all'); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['first', 'second']);
    expect(snapshot.nextCursor).toBe('second-page');
    expect(snapshot.loaded).toBe(true);
    expect(snapshot.loading).toBe(false);
    act(() => { snapshot.setFilter('unread'); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['first']);
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
  });

  it('过期缓存保留内容后台刷新，来回切换复用在途请求，迟到旧版本不回退未读快照', async () => {
    vi.useFakeTimers();
    const oldAll = notification('history', 4, true);
    const oldUnread = notification('unread', 5);
    const allRefresh = deferred<NotificationPage>();
    const unreadRefresh = deferred<NotificationPage>();
    getNotificationsMock
      .mockResolvedValueOnce(page([oldAll, oldUnread], 10))
      .mockResolvedValueOnce(page([oldUnread], 10))
      .mockReturnValueOnce(allRefresh.promise)
      .mockReturnValueOnce(unreadRefresh.promise)
      .mockResolvedValueOnce(page([notification('latest', 11), oldAll], 11));
    render(tree('account-a'));
    await act(async () => {});
    await act(async () => { snapshot.setFilter('unread'); });

    vi.setSystemTime(Date.now() + 15_001);
    act(() => { snapshot.setFilter('all'); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['history', 'unread']);
    expect(snapshot.loaded).toBe(true);
    expect(snapshot.loading).toBe(true);
    act(() => { snapshot.setFilter('unread'); });
    act(() => { snapshot.setFilter('all'); });
    act(() => { snapshot.setFilter('unread'); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(4);
    await act(async () => { unreadRefresh.resolve(page([notification('latest', 11)], 11, { unread_count: 2 })); });
    expect(snapshot.unreadCount).toBe(2);
    await act(async () => { allRefresh.resolve(page([oldAll, oldUnread], 10)); });
    expect(snapshot.unreadCount).toBe(2);
    expect(snapshot.revision).toBe(11);
    await act(async () => { snapshot.setFilter('all'); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(5);
    expect(snapshot.items.map((item) => item.id)).toEqual(['latest', 'history']);
  });

  it('面板重开复用新鲜缓存，过期后只发一个刷新请求，失败保留内容并可强制重试', async () => {
    vi.useFakeTimers();
    const refreshRequest = deferred<NotificationPage>();
    getNotificationsMock.mockResolvedValueOnce(page([notification('first', 5)], 10))
      .mockReturnValueOnce(refreshRequest.promise)
      .mockResolvedValueOnce(page([notification('latest', 11)], 11));
    render(tree('account-a'));
    await act(async () => {});
    act(() => { snapshot.ensureFresh(); snapshot.ensureFresh(); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 15_001);
    act(() => { snapshot.ensureFresh(); snapshot.ensureFresh(); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
    expect(snapshot.items.map((item) => item.id)).toEqual(['first']);
    await act(async () => { refreshRequest.reject(new Error('network failed')); });
    expect(snapshot.error).toBe(true);
    expect(snapshot.loaded).toBe(true);
    await act(async () => { snapshot.refresh(); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
    expect(snapshot.items.map((item) => item.id)).toEqual(['latest']);
  });

  it('全部已读立即同步两份缓存，保留操作版本之后的通知并后台核对', async () => {
    const oldUnread = notification('old', 5);
    const newUnread = notification('new', 11);
    const readRequest = deferred<NotificationReadResponse>();
    const refreshRequest = deferred<NotificationPage>();
    getNotificationsMock
      .mockResolvedValueOnce(page([oldUnread], 10))
      .mockResolvedValueOnce(page([oldUnread], 10))
      .mockResolvedValueOnce(page([newUnread, oldUnread], 11))
      .mockReturnValue(refreshRequest.promise);
    markAllNotificationsReadMock.mockReturnValue(readRequest.promise);
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.filter).toBe('unread'); expect(snapshot.loaded).toBe(true); });
    act(() => { void snapshot.markAllRead(); });
    act(() => { snapshot.refresh(); });
    await waitFor(() => { expect(snapshot.revision).toBe(11); });
    await act(async () => {
      readRequest.resolve({ updated_count: 1, unread_count: 1, unread_conversation_ids: ['chat-new'], revision: 12 });
    });
    expect(snapshot.items.map((item) => item.id)).toEqual(['new']);
    expect(snapshot.unreadCount).toBe(1);
    act(() => { snapshot.setFilter('all'); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['old']);
    expect(snapshot.items[0].read_at).not.toBeNull();
    expect(snapshot.unreadCount).toBe(1);
    expect(snapshot.unreadConversationIds).toEqual(['chat-new']);
    expect(snapshot.loading).toBe(true);
  });

  it('隐藏期间的通用变更也使另一筛选失效，恢复后不复用已删除或已读的旧缓存', async () => {
    getNotificationsMock
      .mockResolvedValueOnce(page([notification('old', 5)], 10))
      .mockResolvedValueOnce(page([notification('old', 5)], 10))
      .mockResolvedValue(page([], 11));
    render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.filter).toBe('unread'); expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.setFilter('all'); });
    visibility = 'hidden';
    act(() => { notifyNotificationsChanged('account-a'); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
    visibility = 'visible';
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    await waitFor(() => { expect(snapshot.items).toEqual([]); });
    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.items).toEqual([]); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(4);
  });

  it('账号切换丢弃两个筛选缓存和在途分页，旧分页迟到不会出现在新账号', async () => {
    const oldPage = deferred<NotificationPage>();
    getNotificationsMock
      .mockResolvedValueOnce(page([notification('old', 5)], 10, { next_cursor: 'old-cursor' }))
      .mockResolvedValueOnce(page([notification('old', 5)], 10))
      .mockReturnValueOnce(oldPage.promise)
      .mockResolvedValueOnce(page([notification('new', 2)], 2))
      .mockResolvedValueOnce(page([notification('new', 2)], 2));
    const view = render(tree('account-a'));
    await waitFor(() => { expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.filter).toBe('unread'); expect(snapshot.loaded).toBe(true); });
    act(() => { snapshot.setFilter('all'); snapshot.loadMore(); });
    const oldSignal = getNotificationsMock.mock.calls[2][1] as AbortSignal;
    view.rerender(tree('account-b'));
    expect(oldSignal.aborted).toBe(true);
    await waitFor(() => { expect(snapshot.items.map((item) => item.id)).toEqual(['new']); });
    await act(async () => { oldPage.resolve(page([notification('late-old', 99)], 99)); });
    expect(snapshot.revision).toBe(2);
    act(() => { snapshot.setFilter('unread'); });
    await waitFor(() => { expect(snapshot.filter).toBe('unread'); expect(snapshot.loaded).toBe(true); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['new']);
    expect(getNotificationsMock).toHaveBeenCalledTimes(5);
  });

  it('切换后轮询保留已展开页，刷新途中版本变化时重取首屏而不拼接旧页', async () => {
    vi.useFakeTimers();
    getNotificationsMock
      .mockResolvedValueOnce(page([notification('first', 5)], 10, { next_cursor: 'first-page' }))
      .mockResolvedValueOnce(page([notification('second', 4, true)], 10, { next_cursor: 'second-page' }))
      .mockResolvedValueOnce(page([notification('first', 5)], 10))
      .mockResolvedValueOnce(page([notification('first', 5)], 10, { next_cursor: 'first-page' }))
      .mockResolvedValueOnce(page([notification('second', 4, true)], 11))
      .mockResolvedValueOnce(page([notification('latest', 11)], 11));
    render(tree('account-a'));
    await act(async () => {});
    await act(async () => { snapshot.loadMore(); });
    await act(async () => { snapshot.setFilter('unread'); });
    act(() => { snapshot.setFilter('all'); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['first', 'second']);

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(6);
    expect(getNotificationsMock.mock.calls[4][0]).toEqual({ filter: 'all', cursor: 'first-page', limit: 20 });
    expect(getNotificationsMock.mock.calls[5][0]).toEqual({ filter: 'all', limit: 20 });
    expect(snapshot.items.map((item) => item.id)).toEqual(['latest']);
    expect(snapshot.revision).toBe(11);
    expect(snapshot.nextCursor).toBeNull();
  });

  it('两个筛选首次请求可并行并去重，后台筛选响应不覆盖当前列表，卸载后中止所有查询', async () => {
    const allRequest = deferred<NotificationPage>();
    const unreadRequest = deferred<NotificationPage>();
    const refreshRequest = deferred<NotificationPage>();
    getNotificationsMock.mockReturnValueOnce(allRequest.promise)
      .mockReturnValueOnce(unreadRequest.promise)
      .mockReturnValueOnce(refreshRequest.promise);
    const view = render(tree('account-a'));
    act(() => { snapshot.setFilter('unread'); });
    act(() => { snapshot.setFilter('all'); });
    act(() => { snapshot.setFilter('unread'); snapshot.ensureFresh(); });
    expect(getNotificationsMock).toHaveBeenCalledTimes(2);
    await act(async () => { allRequest.resolve(page([notification('history', 5, true)], 10)); });
    expect(snapshot.filter).toBe('unread');
    expect(snapshot.items).toEqual([]);
    expect(snapshot.loading).toBe(true);
    act(() => { snapshot.setFilter('all'); snapshot.refresh(); });
    expect(snapshot.items.map((item) => item.id)).toEqual(['history']);
    const allSignal = getNotificationsMock.mock.calls[2][1] as AbortSignal;
    const unreadSignal = getNotificationsMock.mock.calls[1][1] as AbortSignal;
    view.unmount();
    expect(allSignal.aborted).toBe(true);
    expect(unreadSignal.aborted).toBe(true);
    await act(async () => {
      refreshRequest.resolve(page([notification('late-all', 99)], 99));
      unreadRequest.resolve(page([notification('late-unread', 98)], 98));
    });
    expect(getNotificationsMock).toHaveBeenCalledTimes(3);
  });
});
