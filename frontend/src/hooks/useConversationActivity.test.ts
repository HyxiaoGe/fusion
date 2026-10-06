import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getConversationActivity, markConversationRead } from '@/lib/api/chat';
import { ACTIVITY_POLL_INTERVAL_MS, useConversationActivity } from './useConversationActivity';

vi.mock('@/lib/api/chat', () => ({
  getConversationActivity: vi.fn(),
  markConversationRead: vi.fn(),
}));

const mockedGet = vi.mocked(getConversationActivity);
const mockedRead = vi.mocked(markConversationRead);
const NO_LOCAL: readonly string[] = [];

describe('useConversationActivity', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedRead.mockReset();
    mockedRead.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('merges server streaming ids with local ones and hides unread for streaming and active chats', async () => {
    mockedGet.mockResolvedValue({ streaming: ['remote'], unread: ['done', 'remote', 'active'] });

    const { result } = renderHook(() => useConversationActivity('active', ['local']));

    await waitFor(() => expect(result.current.streamingConversationIds).toEqual(['local', 'remote']));
    expect(result.current.unreadConversationIds).toEqual(['done']);
  });

  it('marks the open conversation as read once', async () => {
    mockedGet.mockResolvedValue({ streaming: [], unread: ['conv-1'] });

    const { result, rerender } = renderHook(
      ({ active }: { active: string | null }) => useConversationActivity(active, NO_LOCAL),
      { initialProps: { active: null as string | null } },
    );
    await waitFor(() => expect(result.current.unreadConversationIds).toEqual(['conv-1']));

    rerender({ active: 'conv-1' });
    await waitFor(() => expect(mockedRead).toHaveBeenCalledWith('conv-1'));

    // 服务端还没来得及清掉时再拉到同一条，不重复请求。
    await act(async () => {
      await result.current.refresh();
    });
    expect(mockedRead).toHaveBeenCalledTimes(1);
    rerender({ active: null });
    expect(result.current.unreadConversationIds).toEqual([]);
  });

  it('refreshes when a local stream ends', async () => {
    mockedGet.mockResolvedValue({ streaming: [], unread: [] });
    const { rerender } = renderHook(
      ({ local }: { local: readonly string[] }) => useConversationActivity(null, local),
      { initialProps: { local: ['conv-1'] as readonly string[] } },
    );
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));

    mockedGet.mockResolvedValue({ streaming: [], unread: ['conv-1'] });
    rerender({ local: NO_LOCAL });

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
  });

  it('polls only while something is streaming', async () => {
    vi.useFakeTimers();
    mockedGet.mockResolvedValue({ streaming: [], unread: [] });
    renderHook(() => useConversationActivity(null, NO_LOCAL));
    await act(async () => {
      await Promise.resolve();
    });
    expect(mockedGet).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(ACTIVITY_POLL_INTERVAL_MS * 3);
    });
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('keeps the last state when a refresh fails', async () => {
    mockedGet.mockResolvedValueOnce({ streaming: ['conv-1'], unread: [] });
    const { result } = renderHook(() => useConversationActivity(null, NO_LOCAL));
    await waitFor(() => expect(result.current.streamingConversationIds).toEqual(['conv-1']));

    mockedGet.mockRejectedValueOnce(new Error('503'));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.streamingConversationIds).toEqual(['conv-1']);
  });
});
