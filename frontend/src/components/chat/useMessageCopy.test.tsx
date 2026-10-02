import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useMessageCopy } from './useMessageCopy';
import i18n from '@/lib/i18n';

const toastMock = vi.fn();

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));

describe('useMessageCopy', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    vi.useFakeTimers();
    toastMock.mockReset();
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
      configurable: true,
    });
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(() => true),
      configurable: true,
    });
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('secure context 下使用 clipboard API 成功复制文本', async () => {
    const { result } = renderHook(() => useMessageCopy({ text: '复制内容' }));

    await act(async () => {
      await result.current.copy();
    });

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('复制内容');
    expect(result.current.copied).toBe(true);
    expect(toastMock).not.toHaveBeenCalled();
  });

  it('复制失败时显示错误 toast', async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error('blocked'));
    const { result } = renderHook(() => useMessageCopy({ text: '复制失败' }));

    await act(async () => {
      await result.current.copy();
    });

    expect(toastMock).toHaveBeenCalledWith({
      message: '复制失败，请重试',
      type: 'error',
    });
    expect(result.current.copied).toBe(false);
  });

  it('空文本直接返回且不触发 toast', async () => {
    const { result } = renderHook(() => useMessageCopy({ text: '' }));

    await act(async () => {
      await result.current.copy();
    });

    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
    expect(document.execCommand).not.toHaveBeenCalled();
    expect(toastMock).not.toHaveBeenCalled();
    expect(result.current.copied).toBe(false);
  });

  it('消息编辑后清除旧成功状态，迟到的复制结果不能标记新内容', async () => {
    let finishCopy!: () => void;
    vi.mocked(navigator.clipboard.writeText).mockImplementationOnce(() => new Promise<void>(resolve => { finishCopy = resolve; }));
    const { result, rerender } = renderHook(({ text }) => useMessageCopy({ text }), { initialProps: { text: '编辑前' } });
    let pending!: Promise<void>;
    act(() => { pending = result.current.copy(); });
    rerender({ text: '编辑后' });
    await act(async () => { finishCopy(); await pending; });
    expect(result.current.copied).toBe(false);
    expect(vi.getTimerCount()).toBe(0);

    await act(async () => { await result.current.copy(); });
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith('编辑后');
    expect(result.current.copied).toBe(true);
    rerender({ text: '再次编辑' });
    expect(result.current.copied).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('卸载后迟到的复制失败不弹出旧消息错误', async () => {
    let failCopy!: (error: Error) => void;
    vi.mocked(navigator.clipboard.writeText).mockImplementationOnce(() => new Promise<void>((_, reject) => { failCopy = reject; }));
    const { result, unmount } = renderHook(() => useMessageCopy({ text: '旧消息' }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.copy(); });
    unmount();
    await act(async () => { failCopy(new Error('blocked')); await pending; });
    expect(toastMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('成功复制后 2 秒重置 copied，并以最后一次复制重新计时', async () => {
    const { result } = renderHook(() => useMessageCopy({ text: '计时内容' }));

    await act(async () => {
      await result.current.copy();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      await result.current.copy();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(result.current.copied).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(result.current.copied).toBe(false);
  });

  it('非 secure context 下使用 textarea fallback 并移除临时节点', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      value: false,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    });
    const { result } = renderHook(() => useMessageCopy({ text: 'fallback 内容' }));

    await act(async () => {
      await result.current.copy();
    });

    expect(document.execCommand).toHaveBeenCalledWith('copy');
    expect(document.querySelector('textarea')).toBeNull();
    expect(result.current.copied).toBe(true);
  });
});
