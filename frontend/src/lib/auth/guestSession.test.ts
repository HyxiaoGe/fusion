import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearGuestSession, ensureGuestSession, getStoredGuestToken } from './guestSession';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

describe('guestSession', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    clearGuestSession();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('名额用完等错误返回不可用并带上服务端提示', async () => {
    fetchMock.mockResolvedValue(json(429, { code: 'GUEST_LIMIT_REACHED', message: '今天免登录名额已用完' }));
    await expect(ensureGuestSession()).resolves.toEqual({
      status: 'unavailable',
      code: 'GUEST_LIMIT_REACHED',
      message: '今天免登录名额已用完',
    });
  });

  it('领取后存下令牌，再次调用直接复用，并发调用只发一次请求', async () => {
    fetchMock.mockImplementation(async () => json(200, {
      code: 'SUCCESS',
      data: { access_token: 'guest-token', expires_at: Date.now() / 1000 + 3600, user: { id: 'guest-1' } },
    }));

    const [first, second] = await Promise.all([ensureGuestSession(), ensureGuestSession()]);
    expect(first).toEqual(second);
    expect(first).toMatchObject({ status: 'ready', session: { token: 'guest-token', userId: 'guest-1' } });
    expect(getStoredGuestToken()).toBe('guest-token');

    await ensureGuestSession();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('游客模式未开放时返回不可用，不存令牌', async () => {
    fetchMock.mockResolvedValue(json(200, { code: 'SUCCESS', data: { available: false } }));

    await expect(ensureGuestSession()).resolves.toEqual({
      status: 'unavailable',
      code: 'GUEST_MODE_DISABLED',
      message: '暂未开放免登录使用',
    });
    expect(getStoredGuestToken()).toBeNull();
  });

  it('过期的游客令牌不再使用', () => {
    localStorage.setItem('guest_session', JSON.stringify({
      token: 'old', userId: 'guest-1', expiresAt: Date.now() / 1000 - 10,
    }));
    expect(getStoredGuestToken()).toBeNull();
  });
});
