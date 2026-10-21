import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { syncConversationActivity } from '@/lib/api/sync';
import { ACTIVITY_POLL_INTERVAL_MS, useConversationActivity } from './useConversationActivity';

vi.mock('@/lib/api/sync', () => ({
  syncConversationActivity: vi.fn(),
}));

const mockedGet = vi.mocked(syncConversationActivity);
const NO_LOCAL: readonly string[] = [];

describe('useConversationActivity', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('merges server and local streams', async () => {
    mockedGet.mockResolvedValue({ streaming: ['remote'] });

    const { result } = renderHook(() => useConversationActivity('user-1', ['local']));

    await waitFor(() => expect(result.current.streamingConversationIds).toEqual(['local', 'remote']));
  });

  it('refreshes when a local stream ends', async () => {
    mockedGet.mockResolvedValue({ streaming: [] });
    const { rerender } = renderHook(
      ({ local }: { local: readonly string[] }) => useConversationActivity('user-1', local),
      { initialProps: { local: ['conv-1'] as readonly string[] } },
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));

    mockedGet.mockResolvedValue({ streaming: [] });
    rerender({ local: NO_LOCAL });

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });

  it('本页流结束后不采纳服务端尚未撤下的进行中，服务端撤下后恢复采纳', async () => {
    mockedGet.mockResolvedValue({ streaming: ['conv-1'] });
    const { result, rerender } = renderHook(
      ({ local }: { local: readonly string[] }) => useConversationActivity('user-1', local),
      { initialProps: { local: ['conv-1'] as readonly string[] } },
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);

    // 服务端还在发推荐问题，仍登记为进行中。
    rerender({ local: NO_LOCAL });
    expect(result.current.streamingConversationIds).toEqual([]);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    expect(result.current.streamingConversationIds).toEqual([]);

    // 服务端撤下后，之后（例如另一个标签页）再登记的进行中照常显示。
    mockedGet.mockResolvedValue({ streaming: [] });
    await act(async () => {
      await result.current.refresh();
    });
    mockedGet.mockResolvedValue({ streaming: ['conv-1'] });
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);
  });

  it('本页放手的流（页面卸载收掉恢复流）仍以服务端为准，侧栏继续转圈', async () => {
    vi.useFakeTimers();
    mockedGet.mockResolvedValue({ streaming: ['conv-1'] });
    const settled = vi.fn();
    const { result, rerender } = renderHook(
      ({ local, detached }: { local: readonly string[]; detached: readonly string[] }) =>
        useConversationActivity('user-1', local, settled, detached),
      { initialProps: { local: ['conv-1'] as readonly string[], detached: NO_LOCAL } },
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);

    // 刷新后续上的恢复流随页面卸载被收掉，后台运行还在继续。
    rerender({ local: NO_LOCAL, detached: ['conv-1'] });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);
    expect(settled).not.toHaveBeenCalled();

    // 仍按节拍轮询，服务端撤下时才算结束。
    const callsBefore = mockedGet.mock.calls.length;
    mockedGet.mockResolvedValue({ streaming: [] });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ACTIVITY_POLL_INTERVAL_MS);
    });
    expect(mockedGet.mock.calls.length).toBeGreaterThan(callsBefore);
    expect(result.current.streamingConversationIds).toEqual([]);
    expect(settled).toHaveBeenCalledWith(['conv-1']);
  });

  it('polls only while something is streaming', async () => {
    vi.useFakeTimers();
    mockedGet.mockResolvedValue({ streaming: [] });
    renderHook(() => useConversationActivity('user-1', NO_LOCAL));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockedGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(ACTIVITY_POLL_INTERVAL_MS * 3);
    });
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('does not request anything before login and clears state on logout', async () => {
    mockedGet.mockResolvedValue({ streaming: ['conv-1'] });
    const { result, rerender } = renderHook(
      ({ session }: { session: string | null }) => useConversationActivity(session, NO_LOCAL),
      { initialProps: { session: null as string | null } },
    );
    await act(async () => {
      await result.current.refresh();
    });
    expect(mockedGet).not.toHaveBeenCalled();

    rerender({ session: 'user-1' });
    await waitFor(() => expect(result.current.streamingConversationIds).toEqual(['conv-1']));

    rerender({ session: null });
    expect(result.current.streamingConversationIds).toEqual([]);
  });

  it('reports conversations that leave the streaming set, but not on logout', async () => {
    mockedGet.mockResolvedValue({ streaming: [] });
    const onSettled = vi.fn();
    const { rerender } = renderHook(
      ({ session, local }: { session: string | null; local: readonly string[] }) =>
        useConversationActivity(session, local, onSettled),
      { initialProps: { session: 'user-1' as string | null, local: ['conv-1', 'conv-2'] as readonly string[] } },
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalled());

    rerender({ session: 'user-1', local: ['conv-2'] });
    expect(onSettled).toHaveBeenCalledWith(['conv-1']);

    onSettled.mockClear();
    rerender({ session: null, local: [] });
    expect(onSettled).not.toHaveBeenCalled();
  });

  it('keeps the last state when a refresh fails', async () => {
    mockedGet.mockResolvedValueOnce({ streaming: ['conv-1'] });
    const { result } = renderHook(() => useConversationActivity('user-1', NO_LOCAL));
    await waitFor(() => expect(result.current.streamingConversationIds).toEqual(['conv-1']));

    mockedGet.mockRejectedValueOnce(new Error('503'));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);
  });
});
