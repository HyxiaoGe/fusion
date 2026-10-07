import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import { ApiError } from '@/types/api';
import type { ChangelogDetail, ChangelogPage } from '@/lib/api/changelogs';

const { identity, getList, getDetail, read } = vi.hoisted(() => ({ identity: { sessionKey: 'account-a' }, getList: vi.fn(), getDetail: vi.fn(), read: vi.fn() }));
vi.mock('@/components/notifications/NotificationsProvider', () => ({ useNotifications: () => identity }));
vi.mock('@/lib/api/changelogs', () => ({ getChangelogs: getList, getChangelog: getDetail }));
vi.mock('@/lib/api/notifications', () => ({ markNotificationsRead: read }));
import ChangelogList from './ChangelogList';
import ChangelogReader from './ChangelogReader';

const detail: ChangelogDetail = { id: 'release-a', version: '1.2.0', title: '通知中心上线', summary: '统一查看业务通知', content: '## 自动已读\n\n展示正文后确认阅读。', published_at: '2026-10-07T02:00:00Z', notification_id: 'notice-a' };
let observers: Array<{ callback: IntersectionObserverCallback; target?: Element }>;
function deferred<T>() {
  let resolve!: (result: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function present() {
  await act(async () => {
    const observer = observers.at(-1)!;
    observer.callback([{ target: observer.target, isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  });
}

describe('更新日志用户路径', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    identity.sessionKey = 'account-a';
    getList.mockReset(); getDetail.mockReset(); read.mockReset();
    getList.mockResolvedValue({ items: [detail], next_cursor: null });
    getDetail.mockResolvedValue(detail);
    read.mockResolvedValue({ updated_count: 1, unread_count: 0, revision: 2, unread_conversation_ids: [] });
    observers = [];
    vi.stubGlobal('IntersectionObserver', class {
      private record: (typeof observers)[number];
      constructor(callback: IntersectionObserverCallback) { this.record = { callback }; observers.push(this.record); }
      observe(target: Element) { this.record.target = target; }
      disconnect() {}
    });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each(['list', 'reader'])('服务端中文、浏览器英文时 %s 首帧不产生 hydration 恢复错误', async (page) => {
    const element = page === 'list' ? <ChangelogList /> : <ChangelogReader changelogId="release-a" />;
    const container = document.createElement('div');
    container.innerHTML = renderToString(element);
    document.body.appendChild(container);
    await i18n.changeLanguage('en-US');
    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => { root = hydrateRoot(container, element, { onRecoverableError }); });
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container.querySelector('a')).not.toBeNull();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });

  it('历史列表加载不读通知，列表链接进入相应日志详情', async () => {
    render(<ChangelogList />);
    expect(await screen.findByRole('link', { name: /通知中心上线/ })).toHaveAttribute('href', '/updates/release-a');
    expect(screen.getByText('1.2.0')).toBeVisible();
    expect(read).not.toHaveBeenCalled();
    expect(observers).toHaveLength(0);
  });

  it('分页失败保留已有记录，重试从相同游标继续且不重复日志', async () => {
    getList.mockResolvedValueOnce({ items: [detail], next_cursor: 'release-a' }).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ items: [detail, { ...detail, id: 'release-b', title: '第二篇更新' }], next_cursor: null });
    render(<ChangelogList />);
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('更多通知加载失败');
    expect(screen.getByRole('link', { name: /通知中心上线/ })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('link', { name: /第二篇更新/ })).toBeVisible();
    expect(screen.getAllByRole('link', { name: /通知中心上线/ })).toHaveLength(1);
    expect(getList.mock.calls.slice(1).map((call) => call[0])).toEqual([{ cursor: 'release-a' }, { cursor: 'release-a' }]);
  });

  it('手动进入详情也关联当前用户通知，正文可见后才发送已读', async () => {
    render(<ChangelogReader changelogId="release-a" />);
    expect(await screen.findByRole('heading', { name: '自动已读' })).toBeVisible();
    expect(read).not.toHaveBeenCalled();
    await present();
    expect(read).toHaveBeenCalledWith(['notice-a'], expect.any(AbortSignal));
    expect(screen.getByRole('link', { name: '返回更新日志' })).toHaveAttribute('href', '/updates');
  });

  it('历史日志没有关联通知时仍可阅读，不发送多余回执', async () => {
    getDetail.mockResolvedValue({ ...detail, notification_id: null });
    render(<ChangelogReader changelogId="release-a" />);
    expect(await screen.findByRole('heading', { name: '自动已读' })).toBeVisible();
    expect(observers).toHaveLength(0);
    expect(read).not.toHaveBeenCalled();
  });

  it('详情加载失败保留未读，重试成功后才挂载阅读观察器', async () => {
    getDetail.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(detail);
    render(<ChangelogReader changelogId="release-a" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('更新日志加载失败');
    expect(read).not.toHaveBeenCalled();
    expect(observers).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('heading', { name: '自动已读' })).toBeVisible();
    await present();
    expect(read).toHaveBeenCalledOnce();
  });

  it('不存在的详情不发回执，展示明确缺失状态', async () => {
    getDetail.mockRejectedValue(new ApiError('NOT_FOUND', '更新日志不存在', 'request-a'));
    render(<ChangelogReader changelogId="missing" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('这篇更新日志不存在');
    expect(read).not.toHaveBeenCalled();
  });

  it('账号切换后迟到的列表和详情都不能泄漏旧账号内容或发回执', async () => {
    const oldList = deferred<ChangelogPage>();
    const oldDetail = deferred<ChangelogDetail>();
    getList.mockReturnValueOnce(oldList.promise).mockResolvedValueOnce({ items: [], next_cursor: null });
    getDetail.mockReturnValueOnce(oldDetail.promise).mockResolvedValueOnce({ ...detail, title: '新账号内容', notification_id: null });
    const { rerender } = render(<><ChangelogList /><ChangelogReader changelogId="release-a" /></>);
    identity.sessionKey = 'account-b';
    rerender(<><ChangelogList /><ChangelogReader changelogId="release-a" /></>);
    expect(await screen.findByRole('heading', { name: '新账号内容' })).toBeVisible();
    await act(async () => { oldList.resolve({ items: [detail], next_cursor: null }); oldDetail.resolve(detail); });
    expect(screen.queryByText('通知中心上线')).toBeNull();
    expect(screen.getByText('暂无更新日志')).toBeVisible();
    expect(getList.mock.calls[0][1].aborted).toBe(true);
    expect(getDetail.mock.calls[0][1].aborted).toBe(true);
    expect(read).not.toHaveBeenCalled();
  });

  it('切换日志后旧详情返回不覆盖新日志正文', async () => {
    const old = deferred<ChangelogDetail>();
    getDetail.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...detail, id: 'release-b', title: '第二篇更新', notification_id: 'notice-b' });
    const { rerender } = render(<ChangelogReader changelogId="release-a" />);
    rerender(<ChangelogReader changelogId="release-b" />);
    expect(await screen.findByRole('heading', { name: '第二篇更新' })).toBeVisible();
    await act(async () => old.resolve(detail));
    expect(screen.queryByText('通知中心上线')).toBeNull();
    await present();
    await waitFor(() => expect(read).toHaveBeenCalledWith(['notice-b'], expect.any(AbortSignal)));
  });
});
