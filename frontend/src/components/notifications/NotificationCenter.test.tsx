import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { NotificationItem } from '@/lib/api/notifications';

const { push, state } = vi.hoisted(() => ({
  push: vi.fn(),
  state: {
    sessionKey: 'user-a', filter: 'all', items: [] as NotificationItem[], unreadCount: 0,
    unreadConversationIds: [] as string[], revision: 5, nextCursor: null as string | null,
    loaded: true, loading: false, error: false, loadingMore: false, pageError: false,
    readingAll: false, readError: false, ensureFresh: vi.fn(), refresh: vi.fn(), setFilter: vi.fn(), loadMore: vi.fn(), markAllRead: vi.fn(),
  },
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('./NotificationsProvider', () => ({ useNotifications: () => state }));
import NotificationCenter from './NotificationCenter';

function item(read = false): NotificationItem {
  return {
    id: 'notice-a', business_type: 'ai_conversation', kind: 'run_completed', title: '研究已完成', body: '点此查看结果',
    created_at: '2026-10-07T02:00:00Z', read_at: read ? '2026-10-07T03:00:00Z' : null,
    created_revision: 5, target: { type: 'conversation', conversation_id: 'a/一', run_id: 'run-a', message_id: 'message/一' },
  };
}

describe('通知中心交互', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    Object.assign(state, {
      sessionKey: 'user-a', filter: 'all', items: [item()], unreadCount: 1,
      loaded: true, loading: false, error: false, nextCursor: null,
      loadingMore: false, pageError: false, readingAll: false, readError: false,
    });
    vi.clearAllMocks();
  });
  afterEach(cleanup);

  it('服务端无身份、浏览器恢复身份时首帧一致，挂载后再显示铃铛', async () => {
    state.sessionKey = '';
    const container = document.createElement('div');
    container.innerHTML = renderToString(<NotificationCenter />);
    document.body.appendChild(container);
    expect(container.querySelector('button')).toBeNull();
    state.sessionKey = 'user-a';
    const recoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, <NotificationCenter />, { onRecoverableError: recoverableError });
      });
      expect(screen.getByRole('button', { name: '通知，1 条未读' })).toBeVisible();
      expect(recoverableError).not.toHaveBeenCalled();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });

  it('打开检查缓存新鲜度，点通知关闭并跳到编码后的精确消息，不触发已读', async () => {
    render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    expect(state.ensureFresh).toHaveBeenCalledOnce();
    expect(state.refresh).not.toHaveBeenCalled();
    expect(screen.getByText('AI 对话生成')).toBeVisible();
    expect(screen.getByText('研究已完成')).toBeVisible();
    expect(state.markAllRead).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /研究已完成/ }));
    expect(push).toHaveBeenCalledWith(expect.stringMatching(/^\/chat\/a%2F%E4%B8%80\?message=message%2F%E4%B8%80&run=run-a&notification=\d+-1$/));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(state.markAllRead).not.toHaveBeenCalled();
  });

  it('再次点击相同记录仍发出独立定位请求', async () => {
    render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    fireEvent.click(screen.getByRole('button', { name: /研究已完成/ }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    fireEvent.click(screen.getByRole('button', { name: /研究已完成/ }));
    expect(push).toHaveBeenCalledTimes(2);
    expect(push.mock.calls[0][0]).not.toBe(push.mock.calls[1][0]);
  });

  it('更新日志显示业务类型，点击关闭并进入详情，阅读回执留给正文页面', async () => {
    state.items = [{ ...item(), business_type: 'changelog', kind: 'changelog_published', title: '通知中心上线', target: { type: 'changelog', changelog_id: 'update/一' } }];
    render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    expect(screen.getByText('更新日志')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /通知中心上线/ }));
    expect(push).toHaveBeenCalledWith('/updates?entry=update%2F%E4%B8%80');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(state.markAllRead).not.toHaveBeenCalled();
  });

  it('全部已读后无角标，已读历史仍可查看，Escape 返回铃铛焦点', async () => {
    state.items = [item(true)];
    state.unreadCount = 0;
    render(<NotificationCenter />);
    const trigger = screen.getByRole('button', { name: '通知' });
    expect(screen.queryByTestId('notification-unread-count')).not.toBeInTheDocument();
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByText('研究已完成')).toBeVisible();
    expect(screen.getByText('已读')).toBeVisible();
    expect(screen.getByRole('button', { name: '全部已读' })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('筛选和全部已读使用显式按钮，失败保留记录并可重试', () => {
    state.error = true;
    state.readError = true;
    render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    fireEvent.click(screen.getByRole('button', { name: '未读' }));
    expect(state.setFilter).toHaveBeenCalledWith('unread');
    expect(screen.getByText('通知刷新失败，已保留上次内容。')).toBeVisible();
    expect(screen.getByText('未能标记已读，请重试。')).toBeVisible();
    expect(screen.getByText('研究已完成')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(state.refresh).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '全部已读' }));
    expect(state.markAllRead).toHaveBeenCalledOnce();
  });

  it('初次加载失败显示重试而非空态，成功的未读空列表显示已清空', () => {
    Object.assign(state, { loaded: false, items: [], error: true });
    const { rerender } = render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    expect(screen.getByText('通知加载失败，请重试。')).toBeVisible();
    expect(screen.queryByText('暂无通知')).not.toBeInTheDocument();
    Object.assign(state, { loaded: true, error: false, filter: 'unread', unreadCount: 0 });
    rerender(<NotificationCenter />);
    expect(screen.getByText('没有未读通知')).toBeVisible();
  });

  it('换账号后关闭旧通知面板，不把打开状态带入新账号', async () => {
    const { rerender } = render(<NotificationCenter />);
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }));
    expect(screen.getByRole('dialog')).toBeVisible();
    state.sessionKey = 'user-b';
    rerender(<NotificationCenter />);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
