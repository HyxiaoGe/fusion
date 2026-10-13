import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock('./fetchWithAuth', () => ({
  apiRequest: apiRequestMock,
}));

import {
  resetSyncBatchForTests,
  syncConversationActivity,
  syncConversationViewing,
  syncNotificationsRevision,
} from './sync';

function sentBody(call = 0) {
  return JSON.parse(apiRequestMock.mock.calls[call][1].body);
}

describe('sync batch', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    apiRequestMock.mockReset();
  });

  afterEach(() => {
    resetSyncBatchForTests();
    vi.useRealTimers();
  });

  it('同一轮登记的部分合成一个请求，各自拿到自己的结果', async () => {
    apiRequestMock.mockResolvedValue({
      viewing: true,
      activity: { streaming: ['conv-1'], unread: ['conv-2'] },
      notifications: { revision: 9 },
      failed: [],
    });

    const viewing = syncConversationViewing('conv-1');
    const activity = syncConversationActivity();
    const revision = syncNotificationsRevision();
    await vi.runAllTimersAsync();

    await expect(viewing).resolves.toBeUndefined();
    await expect(activity).resolves.toEqual({ streaming: ['conv-1'], unread: ['conv-2'] });
    await expect(revision).resolves.toBe(9);
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    expect(apiRequestMock.mock.calls[0][0]).toMatch(/\/api\/sync$/);
    expect(sentBody()).toEqual({ viewing_conversation_id: 'conv-1', activity: true, notifications: true });
  });

  it('只请求登记过的部分，下一轮另起一个请求', async () => {
    apiRequestMock.mockResolvedValue({ viewing: null, activity: null, notifications: { revision: 1 }, failed: [] });

    const first = syncNotificationsRevision();
    await vi.runAllTimersAsync();
    await first;
    expect(sentBody()).toEqual({ activity: false, notifications: true });

    const second = syncNotificationsRevision();
    await vi.runAllTimersAsync();
    await second;
    expect(apiRequestMock).toHaveBeenCalledTimes(2);
  });

  it('单个部分失败只拒绝该部分', async () => {
    apiRequestMock.mockResolvedValue({
      viewing: null,
      activity: null,
      notifications: { revision: 2 },
      failed: ['viewing', 'activity'],
    });

    const viewing = syncConversationViewing('conv-1');
    const activity = syncConversationActivity();
    const revision = syncNotificationsRevision();
    const settled = Promise.allSettled([viewing, activity, revision]);
    await vi.runAllTimersAsync();

    const [viewingResult, activityResult, revisionResult] = await settled;
    expect(viewingResult.status).toBe('rejected');
    expect(activityResult.status).toBe('rejected');
    expect(revisionResult).toEqual({ status: 'fulfilled', value: 2 });
  });

  it('整个请求失败时所有部分都拒绝', async () => {
    apiRequestMock.mockRejectedValue(new Error('network'));

    const settled = Promise.allSettled([syncConversationActivity(), syncNotificationsRevision()]);
    await vi.runAllTimersAsync();

    expect((await settled).map(result => result.status)).toEqual(['rejected', 'rejected']);
  });

  it('取消的部分不再发送；整批都取消时不发请求', async () => {
    apiRequestMock.mockResolvedValue({ viewing: null, activity: { streaming: [], unread: [] }, notifications: null, failed: [] });

    const controller = new AbortController();
    const viewing = expect(syncConversationViewing('conv-1', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    const activity = syncConversationActivity();
    controller.abort();
    await vi.runAllTimersAsync();

    await viewing;
    await expect(activity).resolves.toEqual({ streaming: [], unread: [] });
    expect(sentBody()).toEqual({ activity: true, notifications: false });

    const onlyAborted = new AbortController();
    const dropped = expect(syncNotificationsRevision(onlyAborted.signal)).rejects.toMatchObject({ name: 'AbortError' });
    onlyAborted.abort();
    await vi.runAllTimersAsync();
    await dropped;
    expect(apiRequestMock).toHaveBeenCalledTimes(1);
  });
});
