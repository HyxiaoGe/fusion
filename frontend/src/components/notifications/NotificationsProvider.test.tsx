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
    unread_conversation_ids: unread.map((item) => item.target.conversation_id),
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
});
