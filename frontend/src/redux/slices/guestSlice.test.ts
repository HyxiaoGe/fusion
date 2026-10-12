import { beforeEach, describe, expect, it, vi } from 'vitest';

const storeSession = (userId: string) => {
  localStorage.setItem('guest_session', JSON.stringify({
    token: 'guest-token',
    userId,
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
  }));
};

async function loadSlice() {
  vi.resetModules();
  return import('./guestSlice');
}

describe('guestSlice', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('刷新后从本地游客会话恢复身份，首帧即为 ready', async () => {
    storeSession('guest-1');
    const { default: reducer } = await loadSlice();
    expect(reducer(undefined, { type: '@@init' })).toMatchObject({ status: 'ready', userId: 'guest-1' });
  });

  it('没有本地游客会话时保持待领取', async () => {
    const { default: reducer } = await loadSlice();
    expect(reducer(undefined, { type: '@@init' })).toMatchObject({ status: 'idle', userId: null });
  });

  it('已有游客身份时再次领取不退回 loading', async () => {
    storeSession('guest-1');
    const { default: reducer, bootstrapGuestSession } = await loadSlice();
    const state = reducer(undefined, bootstrapGuestSession.pending('request-1'));
    expect(state).toMatchObject({ status: 'ready', userId: 'guest-1' });
  });
});
