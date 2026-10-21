import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TOUCH_INPUT_QUERY, useTouchInput } from './useTouchInput';

const originalMatchMedia = window.matchMedia;

function mockMatchMedia(initial: boolean) {
  let listener: (() => void) | null = null;
  const media = {
    matches: initial,
    media: TOUCH_INPUT_QUERY,
    addEventListener: vi.fn((_: string, cb: () => void) => { listener = cb; }),
    removeEventListener: vi.fn(),
  };
  window.matchMedia = vi.fn(() => media) as unknown as typeof window.matchMedia;
  return {
    media,
    change(next: boolean) {
      media.matches = next;
      listener?.();
    },
  };
}

describe('useTouchInput', () => {
  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it('按触屏媒体查询返回结果，并跟随设备切换更新', () => {
    const control = mockMatchMedia(true);
    const { result, unmount } = renderHook(() => useTouchInput());

    expect(window.matchMedia).toHaveBeenCalledWith(TOUCH_INPUT_QUERY);
    expect(result.current).toBe(true);

    act(() => control.change(false));
    expect(result.current).toBe(false);

    unmount();
    expect(control.media.removeEventListener).toHaveBeenCalled();
  });
});
