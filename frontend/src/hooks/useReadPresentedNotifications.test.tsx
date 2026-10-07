import React, { useRef } from 'react';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { markNotificationResultsRead } from '@/lib/api/notifications';
import { notifyNotificationsChanged } from '@/lib/notifications/events';
import { useReadPresentedNotifications, type PresentedNotificationResult } from './useReadPresentedNotifications';

vi.mock('@/lib/api/notifications', () => ({ markNotificationResultsRead: vi.fn() }));
vi.mock('@/lib/notifications/events', () => ({ notifyNotificationsChanged: vi.fn() }));
const read = vi.mocked(markNotificationResultsRead);
const changed = vi.mocked(notifyNotificationsChanged);
const result: PresentedNotificationResult = { run_id: 'run-a', message_id: 'server-a', domMessageId: 'local-a' };
let observers: Array<{
  callback: IntersectionObserverCallback;
  observe: ReturnType<typeof vi.fn<(element: Element) => void>>;
  disconnect: ReturnType<typeof vi.fn<() => void>>;
}>;
let visibility: DocumentVisibilityState;

function Harness({ enabled = true, session = 'user-1', results = [result], hidden = false }: {
  enabled?: boolean; session?: string; results?: PresentedNotificationResult[]; hidden?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useReadPresentedNotifications(ref, session, 'chat-a', enabled, results);
  return <div data-chat-scroll-container="true" hidden={hidden}><div ref={ref}>
    {results.map(item => <div id={`chat-message-${item.domMessageId}`} key={item.domMessageId}>已保存的结果</div>)}
  </div></div>;
}

async function intersect(isIntersecting = true) {
  const observer = observers.at(-1)!;
  const target = document.getElementById('chat-message-local-a')!;
  await act(async () => {
    observer.callback([{ target, isIntersecting } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
    await vi.advanceTimersByTimeAsync(1);
  });
}

describe('结果展示后的通知已读', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    observers = [];
    visibility = 'visible';
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) {
        observers.push({ callback, observe: vi.fn<(element: Element) => void>(), disconnect: vi.fn<() => void>() });
      }
      observe = (element: Element) => observers.at(-1)!.observe(element);
      disconnect = () => observers.at(-1)!.disconnect();
    });
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0));
    vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id));
    read.mockReset();
    changed.mockReset();
    read.mockResolvedValue({ updated_count: 1, unread_count: 0, unread_conversation_ids: [], revision: 2 });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('只在结果进入视口后提交精确服务端run与message身份', async () => {
    render(<Harness />);
    expect(read).not.toHaveBeenCalled();
    await intersect(false);
    expect(read).not.toHaveBeenCalled();
    await intersect();
    expect(read).toHaveBeenCalledWith({ conversation_id: 'chat-a', results: [{ run_id: 'run-a', message_id: 'server-a' }] }, expect.any(AbortSignal));
    expect(changed).toHaveBeenCalledWith('user-1');
    await intersect();
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('只有缓存、加载失败或聊天面板隐藏时不观察也不已读', () => {
    render(<Harness enabled={false} />);
    expect(observers).toHaveLength(0);
    expect(read).not.toHaveBeenCalled();
  });

  it('后台页面保留未读，回到可见页面后才确认已经展示的结果', async () => {
    visibility = 'hidden';
    render(<Harness />);
    await intersect();
    expect(read).not.toHaveBeenCalled();
    visibility = 'visible';
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('挂载在hidden容器里的结果不能清未读', async () => {
    render(<Harness hidden />);
    await intersect();
    expect(read).not.toHaveBeenCalled();
  });

  it('已读请求失败时保留状态并重试，不提前刷新角标', async () => {
    read.mockRejectedValueOnce(new Error('离线'));
    render(<Harness />);
    await intersect();
    expect(changed).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(15_001); });
    expect(read).toHaveBeenCalledTimes(2);
    expect(changed).toHaveBeenCalledOnce();
  });

  it('换账号时中断旧已读请求，旧响应不能刷新新账号的角标', async () => {
    let resolve!: (value: Awaited<ReturnType<typeof markNotificationResultsRead>>) => void;
    read.mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { rerender } = render(<Harness />);
    await intersect();
    const signal = read.mock.calls[0][1]!;
    rerender(<Harness session="user-2" />);
    expect(signal.aborted).toBe(true);
    await act(async () => { resolve({ updated_count: 1, unread_count: 0, unread_conversation_ids: [], revision: 2 }); });
    expect(changed).not.toHaveBeenCalled();
  });

  it('同一消息后续run保留独立已读身份，旧run确认不会吞掉新结果', async () => {
    const { rerender } = render(<Harness />);
    await intersect();
    rerender(<Harness results={[{ ...result, run_id: 'run-b' }]} />);
    await intersect();
    expect(read.mock.calls[1][0].results).toEqual([{ run_id: 'run-b', message_id: 'server-a' }]);
  });
});
