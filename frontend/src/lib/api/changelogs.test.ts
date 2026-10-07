import { beforeEach, describe, expect, it, vi } from 'vitest';
const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('./fetchWithAuth', () => ({ apiRequest }));
import { getChangelog, getChangelogs } from './changelogs';

describe('更新日志认证客户端', () => {
  beforeEach(() => apiRequest.mockReset());

  it('分页和详情读取传递取消信号，详情 id 编码而非拼成路径', async () => {
    const controller = new AbortController();
    apiRequest.mockResolvedValue({ items: [], next_cursor: null });
    await getChangelogs({ cursor: 'a/一', limit: 30 }, controller.signal);
    expect(apiRequest).toHaveBeenLastCalledWith('/api/changelogs?limit=30&cursor=a%2F%E4%B8%80', { signal: controller.signal });
    await getChangelog('a/一', controller.signal);
    expect(apiRequest).toHaveBeenLastCalledWith('/api/changelogs/a%2F%E4%B8%80', { signal: controller.signal });
  });
});
