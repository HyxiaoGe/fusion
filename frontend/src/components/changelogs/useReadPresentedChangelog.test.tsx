import React, { useRef } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { markNotificationsRead } from '@/lib/api/notifications';
import { notifyNotificationsChanged } from '@/lib/notifications/events';
import { useReadPresentedChangelog } from './useReadPresentedChangelog';

vi.mock('@/lib/api/notifications', () => ({ markNotificationsRead: vi.fn() }));
vi.mock('@/lib/notifications/events', () => ({ notifyNotificationsChanged: vi.fn() }));
const read = vi.mocked(markNotificationsRead);
const changed = vi.mocked(notifyNotificationsChanged);
const response = { updated_count: 1, unread_count: 0, unread_conversation_ids: [], revision: 2 };
let result: ReturnType<typeof useReadPresentedChangelog>;
let visibility: DocumentVisibilityState;
let originalVisibility: PropertyDescriptor | undefined;
let observers: Array<{
  callback: IntersectionObserverCallback;
  observe: ReturnType<typeof vi.fn<(element: Element) => void>>;
  disconnect: ReturnType<typeof vi.fn<() => void>>;
}>;

function Harness({ sessionKey = 'account-a', changelogId = 'release-a', notificationId = 'notification-a', hidden = false }: {
  sessionKey?: string | null;
  changelogId?: string;
  notificationId?: string | null;
  hidden?: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  result = useReadPresentedChangelog({ sessionKey, changelogId, notificationId, bodyRef });
  return <section hidden={hidden}><div data-testid="changelog-body" ref={bodyRef}>更新内容已经加载</div></section>;
}

async function intersect(visible = true, observer = observers.at(-1)!) {
  await act(async () => {
    const target = observer.observe.mock.calls[0][0];
    observer.callback([{ target, isIntersecting: visible } as IntersectionObserverEntry], {} as IntersectionObserver);
  });
}

function deferred() {
  let resolve!: (value: Awaited<ReturnType<typeof markNotificationsRead>>) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<Awaited<ReturnType<typeof markNotificationsRead>>>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

describe('更新日志正文展示后的已读回执', () => {
  beforeEach(() => {
    read.mockReset();
    changed.mockReset();
    read.mockResolvedValue(response);
    observers = [];
    visibility = 'visible';
    originalVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
    vi.stubGlobal('IntersectionObserver', class {
      private observer: (typeof observers)[number];
      constructor(callback: IntersectionObserverCallback) {
        this.observer = { callback, observe: vi.fn<(element: Element) => void>(), disconnect: vi.fn<() => void>() };
        observers.push(this.observer);
      }
      observe = (element: Element) => this.observer.observe(element);
      disconnect = () => this.observer.disconnect();
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    if (originalVisibility) Object.defineProperty(document, 'visibilityState', originalVisibility);
    else Reflect.deleteProperty(document, 'visibilityState');
  });

  it('加载和挂载正文不自动已读，只有进入视口后才提交通知身份，完成后去重', async () => {
    render(<Harness />);
    expect(observers[0].observe).toHaveBeenCalledOnce();
    expect(read).not.toHaveBeenCalled();
    await intersect(false);
    expect(read).not.toHaveBeenCalled();
    await intersect();
    expect(read).toHaveBeenCalledWith(['notification-a'], expect.any(AbortSignal));
    expect(changed).toHaveBeenCalledWith('account-a');
    expect(result.readError).toBe(false);
    await intersect();
    await act(async () => {
      result.retryRead();
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(read).toHaveBeenCalledOnce();
    expect(changed).toHaveBeenCalledOnce();
  });

  it.each([{ sessionKey: null }, { notificationId: null }])('没有当前账号或关联通知时不观察也不提交回执：%o', async (props) => {
    render(<Harness {...props} />);
    await act(async () => {
      result.retryRead();
      window.dispatchEvent(new Event('focus'));
    });
    expect(observers).toHaveLength(0);
    expect(read).not.toHaveBeenCalled();
  });

  it('隐藏页面即使正文在视口也保留未读，恢复可见后确认，不强求滚动全文', async () => {
    visibility = 'hidden';
    render(<Harness />);
    await intersect();
    act(() => { result.retryRead(); });
    expect(read).not.toHaveBeenCalled();
    visibility = 'visible';
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(read).toHaveBeenCalledOnce();
    expect(changed).toHaveBeenCalledWith('account-a');
  });

  it('隐藏容器内的正文不代替实际展示', async () => {
    render(<Harness hidden />);
    await intersect();
    act(() => { result.retryRead(); });
    expect(read).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
  });

  it('失败保留未读并允许手动重试，后台页面或正文离开视口时重试不发送', async () => {
    read.mockRejectedValueOnce(new Error('网络中断'));
    render(<Harness />);
    await intersect();
    expect(result.readError).toBe(true);
    expect(changed).not.toHaveBeenCalled();
    visibility = 'hidden';
    act(() => { result.retryRead(); });
    expect(read).toHaveBeenCalledOnce();
    await intersect(false);
    visibility = 'visible';
    act(() => { result.retryRead(); });
    expect(read).toHaveBeenCalledOnce();
    await intersect();
    expect(result.readError).toBe(false);
    expect(read).toHaveBeenCalledTimes(2);
    expect(changed).toHaveBeenCalledOnce();
  });

  it.each(['manual', 'focus', 'visibilitychange'] as const)('失败后在正文仍可见时由 %s 重试', async (event) => {
    read.mockRejectedValueOnce(new Error('网络中断'));
    render(<Harness />);
    await intersect();
    expect(result.readError).toBe(true);
    await act(async () => {
      if (event === 'manual') result.retryRead();
      else if (event === 'focus') window.dispatchEvent(new Event(event));
      else document.dispatchEvent(new Event(event));
    });
    expect(read).toHaveBeenCalledTimes(2);
    expect(result.readError).toBe(false);
    expect(changed).toHaveBeenCalledOnce();
  });

  it.each([
    { sessionKey: 'account-b', changelogId: 'release-a', notificationId: 'notification-b' },
    { sessionKey: 'account-a', changelogId: 'release-b', notificationId: 'notification-b' },
  ])('账号或日志切换中止旧请求，旧响应不能刷新新页面：%o', async (next) => {
    const oldRequest = deferred();
    read.mockReturnValueOnce(oldRequest.promise);
    const view = render(<Harness />);
    await intersect();
    const oldObserver = observers[0];
    const signal = read.mock.calls[0][1]!;
    view.rerender(<Harness {...next} />);
    expect(signal.aborted).toBe(true);
    expect(oldObserver.disconnect).toHaveBeenCalledOnce();
    await act(async () => { oldRequest.resolve(response); });
    await intersect(true, oldObserver);
    expect(changed).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledOnce();
    await intersect();
    expect(read).toHaveBeenLastCalledWith(['notification-b'], expect.any(AbortSignal));
    expect(changed).toHaveBeenCalledOnce();
    expect(changed).toHaveBeenCalledWith(next.sessionKey);
  });

  it('在途请求去重，卸载中止请求并清理观察器和监听器，旧回调无影响', async () => {
    const request = deferred();
    read.mockReturnValueOnce(request.promise);
    const addWindow = vi.spyOn(window, 'addEventListener');
    const removeWindow = vi.spyOn(window, 'removeEventListener');
    const addDocument = vi.spyOn(document, 'addEventListener');
    const removeDocument = vi.spyOn(document, 'removeEventListener');
    const view = render(<Harness />);
    await intersect();
    await intersect();
    act(() => { result.retryRead(); });
    expect(read).toHaveBeenCalledOnce();
    const signal = read.mock.calls[0][1]!;
    const observer = observers[0];
    const focusCallback = addWindow.mock.calls.find(([name]) => name === 'focus')![1];
    const visibilityCallback = addDocument.mock.calls.find(([name]) => name === 'visibilitychange')![1];
    view.unmount();
    expect(signal.aborted).toBe(true);
    expect(observer.disconnect).toHaveBeenCalledOnce();
    expect(removeWindow).toHaveBeenCalledWith('focus', focusCallback);
    expect(removeDocument).toHaveBeenCalledWith('visibilitychange', visibilityCallback);
    await intersect(true, observer);
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
      request.reject(new Error('请求已中止'));
    });
    expect(changed).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledOnce();
  });
});
