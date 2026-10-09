import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { hydrateRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { ChangelogDetail, ChangelogPage } from '@/lib/api/changelogs';

const { identity, route, getList, read } = vi.hoisted(() => ({
  identity: { sessionKey: 'account-a' },
  route: { entry: null as string | null, back: vi.fn(), push: vi.fn() },
  getList: vi.fn(),
  read: vi.fn(),
}));
vi.mock('@/components/notifications/NotificationsProvider', () => ({ useNotifications: () => identity }));
vi.mock('@/lib/api/changelogs', () => ({ getChangelogs: getList }));
vi.mock('@/lib/api/notifications', () => ({ markNotificationsRead: read }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ back: route.back, push: route.push }),
  useSearchParams: () => new URLSearchParams(route.entry ? { entry: route.entry } : {}),
}));
import ChangelogList from './ChangelogList';

const first: ChangelogDetail = { id: 'release-a', version: '1.2.0', title: '通知中心上线', summary: '统一查看业务通知', content: '## 自动已读\n\n展示正文后确认阅读。', published_at: '2026-10-07T02:00:00Z', notification_id: 'notice-a' };
const second: ChangelogDetail = { ...first, id: 'release-b', version: '1.1.0', title: '第二篇更新', content: '## 旧版本\n\n更早的改动。', notification_id: 'notice-b' };
let observers: Array<{ callback: IntersectionObserverCallback; target?: Element }>;
function deferred<T>() {
  let resolve!: (result: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function present(heading: string) {
  // 每篇正文各自挂一个观察器；找到包含该标题的正文对应的观察器再模拟进入视口。
  const article = (await screen.findByRole('heading', { name: heading, level: 2 })).closest('article')!;
  await waitFor(() => expect(observers.some((observer) => observer.target && article.contains(observer.target))).toBe(true));
  const observer = observers.filter((item) => item.target && article.contains(item.target)).at(-1)!;
  await act(async () => {
    observer.callback([{ target: observer.target, isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  });
}

describe('更新日志独立页', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    identity.sessionKey = 'account-a';
    route.entry = null; route.back.mockReset(); route.push.mockReset();
    getList.mockReset(); read.mockReset();
    getList.mockResolvedValue({ items: [first, second], next_cursor: null });
    read.mockResolvedValue({ updated_count: 1, unread_count: 0, revision: 2, unread_conversation_ids: [] });
    observers = [];
    vi.stubGlobal('IntersectionObserver', class {
      private record: (typeof observers)[number];
      constructor(callback: IntersectionObserverCallback) { this.record = { callback }; observers.push(this.record); }
      observe(target: Element) { this.record.target = target; }
      disconnect() {}
    });
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('服务端中文、浏览器英文时首帧不产生 hydration 恢复错误', async () => {
    const container = document.createElement('div');
    container.innerHTML = renderToString(<ChangelogList />);
    document.body.appendChild(container);
    await i18n.changeLanguage('en-US');
    const onRecoverableError = vi.fn();
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => { root = hydrateRoot(container, <ChangelogList />, { onRecoverableError }); });
      expect(onRecoverableError).not.toHaveBeenCalled();
      expect(container.querySelector('button')).not.toBeNull();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });

  it('不挂聊天侧栏，所有版本完整展开并提供版本目录', async () => {
    render(<ChangelogList />);
    expect(await screen.findByRole('heading', { name: '自动已读' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '旧版本' })).toBeVisible();
    expect(screen.getByRole('heading', { name: '更新日志', level: 1 })).toBeVisible();
    const nav = screen.getByRole('navigation', { name: '版本目录' });
    expect(within(nav).getByRole('link', { name: /1\.2\.0/ })).toHaveAttribute('href', '#changelog-release-a');
    expect(within(nav).getByRole('link', { name: /1\.1\.0/ })).toHaveAttribute('href', '#changelog-release-b');
    expect(document.getElementById('changelog-release-b')).toContainElement(screen.getByRole('heading', { name: '第二篇更新' }));
    expect(read).not.toHaveBeenCalled();
  });

  it('每篇正文各自进入视口后才确认该篇通知', async () => {
    render(<ChangelogList />);
    await present('第二篇更新');
    expect(read).toHaveBeenCalledWith(['notice-b'], expect.any(AbortSignal));
    expect(read).not.toHaveBeenCalledWith(['notice-a'], expect.any(AbortSignal));
    await present('通知中心上线');
    expect(read).toHaveBeenCalledWith(['notice-a'], expect.any(AbortSignal));
  });

  it('没有关联通知的历史日志照常展示，不挂阅读观察器', async () => {
    getList.mockResolvedValue({ items: [{ ...first, notification_id: null }], next_cursor: null });
    render(<ChangelogList />);
    expect(await screen.findByRole('heading', { name: '自动已读' })).toBeVisible();
    expect(observers).toHaveLength(0);
    expect(read).not.toHaveBeenCalled();
  });

  it('从通知进入时定位对应版本，不在首页就继续翻页直到找到', async () => {
    route.entry = 'release-b';
    getList.mockResolvedValueOnce({ items: [first], next_cursor: 'release-a' }).mockResolvedValueOnce({ items: [second], next_cursor: null });
    render(<ChangelogList />);
    expect(await screen.findByRole('heading', { name: '第二篇更新' })).toBeVisible();
    expect(getList.mock.calls[1][0]).toEqual({ cursor: 'release-a' });
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledOnce());
    expect(vi.mocked(Element.prototype.scrollIntoView).mock.contexts[0]).toBe(document.getElementById('changelog-release-b'));
  });

  it('分页失败保留已有记录，重试从相同游标继续且不重复日志', async () => {
    getList.mockResolvedValueOnce({ items: [first], next_cursor: 'release-a' }).mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ items: [first, second], next_cursor: null });
    render(<ChangelogList />);
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('更多通知加载失败');
    expect(screen.getByRole('heading', { name: '通知中心上线' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('heading', { name: '第二篇更新' })).toBeVisible();
    expect(screen.getAllByRole('heading', { name: '通知中心上线' })).toHaveLength(1);
    expect(getList.mock.calls.slice(1).map((call) => call[0])).toEqual([{ cursor: 'release-a' }, { cursor: 'release-a' }]);
  });

  it('列表加载失败可重试', async () => {
    getList.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ items: [first], next_cursor: null });
    render(<ChangelogList />);
    expect(await screen.findByRole('alert')).toHaveTextContent('更新日志加载失败');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByRole('heading', { name: '通知中心上线' })).toBeVisible();
  });

  it('账号切换后迟到的旧列表不能泄漏旧账号内容或发回执', async () => {
    const oldList = deferred<ChangelogPage>();
    getList.mockReturnValueOnce(oldList.promise).mockResolvedValueOnce({ items: [], next_cursor: null });
    const { rerender } = render(<ChangelogList />);
    identity.sessionKey = 'account-b';
    rerender(<ChangelogList />);
    expect(await screen.findByText('暂无更新日志')).toBeVisible();
    await act(async () => { oldList.resolve({ items: [first], next_cursor: null }); });
    expect(screen.queryByText('通知中心上线')).toBeNull();
    expect(getList.mock.calls[0][1].aborted).toBe(true);
    expect(read).not.toHaveBeenCalled();
  });

  it('返回对话优先回到来源页面，直接打开时回到新对话', async () => {
    render(<ChangelogList />);
    const back = await screen.findByRole('button', { name: '返回对话' });
    vi.spyOn(window.history, 'length', 'get').mockReturnValue(3);
    fireEvent.click(back);
    expect(route.back).toHaveBeenCalledOnce();
    vi.spyOn(window.history, 'length', 'get').mockReturnValue(1);
    fireEvent.click(back);
    expect(route.push).toHaveBeenCalledWith('/');
  });
});
