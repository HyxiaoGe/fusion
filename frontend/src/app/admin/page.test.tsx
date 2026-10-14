import { describe, expect, it, vi } from 'vitest';

const redirectMock = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ redirect: redirectMock }));

import AdminIndexPage from './page';

describe('/admin', () => {
  it('默认进入用户列表', async () => {
    await AdminIndexPage({});
    expect(redirectMock).toHaveBeenLastCalledWith('/admin/users');
  });

  it('旧版 ?tab= 链接映射到路径路由', async () => {
    await AdminIndexPage({ searchParams: Promise.resolve({ tab: 'conversations', user_id: 'user-1', model_id: 'm-1' }) });
    expect(redirectMock).toHaveBeenLastCalledWith('/admin/conversations?user_id=user-1&model_id=m-1');
  });
});
