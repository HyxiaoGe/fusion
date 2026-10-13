import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { reportConversationViewing, clearConversationViewing } = vi.hoisted(() => ({
  reportConversationViewing: vi.fn(async () => null),
  clearConversationViewing: vi.fn(async () => null),
}));
vi.mock('@/lib/api/notifications', () => ({ reportConversationViewing, clearConversationViewing }));
import { useReportConversationViewing } from './useReportConversationViewing';

function setVisibility(value: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('对话查看上报', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    setVisibility('visible');
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('可见时立即上报并每 15 秒续期，切到后台撤销', () => {
    renderHook(() => useReportConversationViewing('conv-1', 'user-a'));
    expect(reportConversationViewing).toHaveBeenCalledTimes(1);
    expect(reportConversationViewing).toHaveBeenLastCalledWith('conv-1', expect.any(AbortSignal));

    act(() => { vi.advanceTimersByTime(15_000); });
    expect(reportConversationViewing).toHaveBeenCalledTimes(2);

    act(() => { setVisibility('hidden'); });
    expect(clearConversationViewing).toHaveBeenCalledWith('conv-1');
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(reportConversationViewing).toHaveBeenCalledTimes(2);

    act(() => { setVisibility('visible'); });
    expect(reportConversationViewing).toHaveBeenCalledTimes(3);
  });

  it('切换对话时撤销旧对话并上报新对话', () => {
    const { rerender } = renderHook(({ id }) => useReportConversationViewing(id, 'user-a'), { initialProps: { id: 'conv-1' } });
    rerender({ id: 'conv-2' });

    expect(clearConversationViewing).toHaveBeenCalledWith('conv-1');
    expect(reportConversationViewing).toHaveBeenLastCalledWith('conv-2', expect.any(AbortSignal));
  });

  it('后台打开的页面不上报，未登录也不上报', () => {
    setVisibility('hidden');
    renderHook(() => useReportConversationViewing('conv-1', 'user-a'));
    setVisibility('visible');
    renderHook(() => useReportConversationViewing('conv-2', null));

    expect(reportConversationViewing).toHaveBeenCalledTimes(1);
    expect(reportConversationViewing).toHaveBeenLastCalledWith('conv-1', expect.any(AbortSignal));
  });
});
