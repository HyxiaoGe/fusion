import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { NotificationItem, NotificationPage } from '@/lib/api/notifications';

const { push, dispatch, state, settings, getNotifications } = vi.hoisted(() => ({
  push: vi.fn(),
  dispatch: vi.fn(),
  getNotifications: vi.fn(),
  settings: { isNotificationsDialogOpen: true },
  state: {
    sessionKey: 'user-a', revision: 5, unreadCount: 1, loaded: true,
    readingAll: false, readError: false, markAllRead: vi.fn(),
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('./NotificationsProvider', () => ({ useNotifications: () => state }));
vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => dispatch,
  useAppSelector: (selector: (root: { settings: typeof settings }) => unknown) => selector({ settings }),
}));
vi.mock('@/lib/api/notifications', () => ({ getNotifications }));
import NotificationsDialog from './NotificationsDialog';
import { closeNotificationsDialog } from '@/redux/slices/settingsSlice';

function item(id: string): NotificationItem {
  return {
    id, business_type: 'ai_conversation', kind: 'run_completed', title: `结果 ${id}`, body: '',
    created_at: '2026-10-13T02:00:00Z', read_at: null, created_revision: 1,
    target: { type: 'conversation', conversation_id: `conv-${id}`, run_id: `run-${id}`, message_id: `msg-${id}` },
  };
}

function page(items: NotificationItem[], nextCursor: string | null): NotificationPage {
  return { items, next_cursor: nextCursor, unread_count: 1, unread_conversation_ids: [], revision: 5 };
}

describe('消息盒子', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    vi.clearAllMocks();
    settings.isNotificationsDialogOpen = true;
    state.revision = 5;
  });
  afterEach(cleanup);

  it('按游标翻到下一页，再回到上一页', async () => {
    getNotifications.mockImplementation(async ({ cursor }: { cursor?: string }) => (
      cursor === 'c2' ? page([item('b')], null) : page([item('a')], 'c2')
    ));
    render(<NotificationsDialog />);

    expect(await screen.findByText('结果 a')).toBeVisible();
    expect(screen.getByText('第 1 页')).toBeVisible();
    expect(screen.getByRole('button', { name: /上一页/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /下一页/ }));
    expect(await screen.findByText('结果 b')).toBeVisible();
    expect(screen.getByText('第 2 页')).toBeVisible();
    expect(screen.getByRole('button', { name: /下一页/ })).toBeDisabled();
    expect(getNotifications).toHaveBeenLastCalledWith({ filter: 'all', cursor: 'c2', limit: 10 }, expect.any(AbortSignal));

    fireEvent.click(screen.getByRole('button', { name: /上一页/ }));
    expect(await screen.findByText('结果 a')).toBeVisible();
    expect(screen.getByText('第 1 页')).toBeVisible();
  });

  it('切到未读从第一页重新取', async () => {
    getNotifications.mockResolvedValue(page([item('a')], 'c2'));
    render(<NotificationsDialog />);
    await screen.findByText('结果 a');
    fireEvent.click(screen.getByRole('button', { name: /下一页/ }));
    await screen.findByText('第 2 页');

    fireEvent.click(screen.getByRole('button', { name: '未读' }));

    await waitFor(() => expect(getNotifications).toHaveBeenLastCalledWith(
      { filter: 'unread', cursor: undefined, limit: 10 }, expect.any(AbortSignal),
    ));
    expect(screen.getByText('第 1 页')).toBeVisible();
  });

  it('点通知关闭窗口并跳到对应回答', async () => {
    getNotifications.mockResolvedValue(page([item('a')], null));
    render(<NotificationsDialog />);
    fireEvent.click(await screen.findByRole('button', { name: /结果 a/ }));

    expect(dispatch).toHaveBeenCalledWith(closeNotificationsDialog());
    expect(push).toHaveBeenCalledWith(expect.stringMatching(/^\/chat\/conv-a\?message=msg-a&run=run-a&notification=/));
  });

  it('关闭时不取数据', () => {
    settings.isNotificationsDialogOpen = false;
    render(<NotificationsDialog />);
    expect(getNotifications).not.toHaveBeenCalled();
  });
});
