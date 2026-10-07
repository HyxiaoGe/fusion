import { beforeEach, describe, expect, it, vi } from 'vitest';
const apiRequestMock = vi.hoisted(() => vi.fn());
vi.mock('./fetchWithAuth', () => ({ apiRequest: apiRequestMock }));
import { getNotifications, markAllNotificationsRead, markNotificationResultsRead, markNotificationsRead } from './notifications';

describe('通知 API 客户端', () => {
  beforeEach(() => apiRequestMock.mockReset());

  it('筛选、游标和取消信号经认证客户端传递', async () => {
    const controller = new AbortController();
    const page = { items: [], unread_count: 3, unread_conversation_ids: ['a'], revision: 7, next_cursor: '2' };
    apiRequestMock.mockResolvedValue(page);
    await expect(getNotifications({ filter: 'unread', cursor: '5', limit: 30 }, controller.signal)).resolves.toBe(page);
    expect(apiRequestMock).toHaveBeenCalledWith('/api/notifications?filter=unread&limit=30&cursor=5', { signal: controller.signal });
  });

  it('全部已读提交当前快照截止版本，精确结果以批量三元身份提交', async () => {
    const controller = new AbortController();
    const result = { updated_count: 1, unread_count: 0, unread_conversation_ids: [], revision: 8 };
    apiRequestMock.mockResolvedValue(result);
    await expect(markAllNotificationsRead(7, controller.signal)).resolves.toBe(result);
    expect(apiRequestMock).toHaveBeenLastCalledWith('/api/notifications/read-all', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ through_revision: 7 }), signal: controller.signal,
    });
    const body = { conversation_id: 'conversation-a', results: [{ run_id: 'run-a', message_id: 'message-a' }] };
    await markNotificationResultsRead(body, controller.signal);
    expect(apiRequestMock).toHaveBeenLastCalledWith('/api/notifications/read-result', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal,
    });
    await markNotificationsRead(['notice-a']);
    expect(apiRequestMock).toHaveBeenLastCalledWith('/api/notifications/read', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: ['notice-a'] }), signal: undefined,
    });
  });
});
