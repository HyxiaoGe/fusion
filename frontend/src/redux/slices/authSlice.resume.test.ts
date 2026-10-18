import { configureStore } from '@reduxjs/toolkit';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  resumeCentralSessionMock,
  getStoredAccessTokenMock,
  fetchUserProfileAPIMock,
  clearFusionProfileStorageMock,
} = vi.hoisted(() => ({
  resumeCentralSessionMock: vi.fn(),
  getStoredAccessTokenMock: vi.fn<() => string | null>(() => null),
  fetchUserProfileAPIMock: vi.fn(),
  clearFusionProfileStorageMock: vi.fn(),
}));

vi.mock('@/lib/auth/authService', () => ({
  resumeCentralSession: resumeCentralSessionMock,
  getStoredAccessToken: getStoredAccessTokenMock,
  clearAuthStorage: vi.fn(),
  clearFusionProfileStorage: clearFusionProfileStorageMock,
  completeSsoCallback: vi.fn(),
  getValidAccessToken: vi.fn(),
  probeSessionLiveness: vi.fn(),
  reconcileSsoSession: vi.fn(),
  revokeSsoSession: vi.fn(),
  clearRemoteSsoSession: vi.fn(),
}));

vi.mock('@/lib/auth/sso-probe', () => ({
  markExplicitLogout: vi.fn(),
}));

vi.mock('../../lib/api/user', () => ({
  fetchUserProfileAPI: fetchUserProfileAPIMock,
  updateUserSettingsAPI: vi.fn(),
}));

import {
  beginAuthSessionTransition,
  getAuthSessionTransitionState,
  resetAuthSessionTransitionForTests,
} from '@/lib/auth/sessionTransition';
import { accountSessionSwitchCompleted, accountSessionSwitchStarted } from '@/redux/actions/authSessionActions';
import authReducer, {
  adoptCommittedSsoSession,
  resumeSsoSession,
  setToken,
  settleSdkUnauthenticatedSession,
} from './authSlice';

const PROFILE = {
  id: 'user-resumed',
  username: 'resumed',
  email: 'resumed@example.com',
  nickname: '恢复用户',
  avatar: null,
  mobile: null,
  system_prompt: '',
  is_superuser: false,
};

function tokenFor(subject: string): string {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'RS256', typ: 'JWT' })}.${encode({
    sub: subject,
    email: PROFILE.email,
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}.signature`;
}

describe('resumeSsoSession thunk', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    resetAuthSessionTransitionForTests();
    fetchUserProfileAPIMock.mockResolvedValue(PROFILE);
  });

  it('closes an already-adopted sibling session transition instead of leaving the overlay spinning', async () => {
    const token = tokenFor(PROFILE.id);
    getStoredAccessTokenMock.mockReturnValue(token);
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(setToken(token));
    beginAuthSessionTransition();
    store.dispatch(accountSessionSwitchStarted());

    const result = await store.dispatch(adoptCommittedSsoSession({
      email: PROFILE.email,
    })).unwrap();

    expect(result).toBe(token);
    expect(getAuthSessionTransitionState()).toBe('stable');
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
    // 账户早已是这个身份，重复同步只关遮罩，不再提示「已切换」；只有 token 最小身份时补拉资料。
    expect(store.getState().auth.switchedAccountEmail).toBeNull();
    expect(fetchUserProfileAPIMock).toHaveBeenCalledTimes(1);
    expect(store.getState().auth.user?.nickname).toBe(PROFILE.nickname);
  });

  it('重复同步中止了前一次采用的资料请求时，关闭屏障并补拉资料（头像不丢）', async () => {
    const token = tokenFor(PROFILE.id);
    getStoredAccessTokenMock.mockReturnValue(token);
    const profileWithAvatar = { ...PROFILE, avatar: 'https://lh3.googleusercontent.com/a/x=s96-c' };
    let abortFirstFetch: (error: Error) => void = () => undefined;
    fetchUserProfileAPIMock
      .mockImplementationOnce(() => new Promise((_, reject) => { abortFirstFetch = reject; }))
      .mockResolvedValue(profileWithAvatar);
    const completions: Array<string | null> = [];
    const store = configureStore({
      reducer: { auth: authReducer },
      middleware: getDefault => getDefault().concat(() => next => (action) => {
        if (accountSessionSwitchCompleted.match(action)) completions.push(action.payload.email);
        return next(action);
      }),
    });

    // 第一次同步：采用新账户，资料请求在途。
    beginAuthSessionTransition();
    store.dispatch(accountSessionSwitchStarted());
    const first = store.dispatch(adoptCommittedSsoSession({ email: PROFILE.email }));
    // 第二次同步（switched 事件）重新开启屏障，中止在途资料请求。
    beginAuthSessionTransition();
    store.dispatch(accountSessionSwitchStarted());
    const second = store.dispatch(adoptCommittedSsoSession({ email: PROFILE.email }));
    abortFirstFetch(new Error('aborted by account transition'));
    await Promise.all([first, second]);

    expect(getAuthSessionTransitionState()).toBe('stable');
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
    expect(store.getState().auth.user?.avatar).toBe(profileWithAvatar.avatar);
    expect(fetchUserProfileAPIMock).toHaveBeenCalledTimes(2);
    expect(completions).toEqual([PROFILE.email]);
  });

  it('兄弟标签换号后本标签同步两次（token 出口 + switched 事件），只提示一次', async () => {
    const token = tokenFor(PROFILE.id);
    getStoredAccessTokenMock.mockReturnValue(token);
    const store = configureStore({ reducer: { auth: authReducer } });

    beginAuthSessionTransition();
    store.dispatch(accountSessionSwitchStarted());
    await store.dispatch(adoptCommittedSsoSession({ email: PROFILE.email }));
    expect(store.getState().auth.switchedAccountEmail).toBe(PROFILE.email);
    // ClientLayout 显示提示后清空。
    store.dispatch(accountSessionSwitchCompleted({ email: null }));

    beginAuthSessionTransition();
    store.dispatch(accountSessionSwitchStarted());
    await store.dispatch(adoptCommittedSsoSession({ email: PROFILE.email }));

    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
    expect(getAuthSessionTransitionState()).toBe('stable');
    expect(store.getState().auth.switchedAccountEmail).toBeNull();
    expect(fetchUserProfileAPIMock).toHaveBeenCalledTimes(1);
  });

  it('SDK 订阅与 resume 并发采用同一次提交时，拉一次资料、只完成一次切换', async () => {
    const token = tokenFor(PROFILE.id);
    const completions: Array<string | null> = [];
    const store = configureStore({
      reducer: { auth: authReducer },
      middleware: getDefault => getDefault().concat(() => next => (action) => {
        if (accountSessionSwitchCompleted.match(action)) completions.push(action.payload.email);
        return next(action);
      }),
    });
    resumeCentralSessionMock.mockImplementation(async (options) => {
      await options?.beforeCommit?.({ user: PROFILE });
      // SDK 提交后同步通知订阅者，ClientLayout 先发起一次采用。
      getStoredAccessTokenMock.mockReturnValue(token);
      void store.dispatch(adoptCommittedSsoSession({ email: PROFILE.email }));
      return { status: 'resumed', user: PROFILE };
    });

    await store.dispatch(resumeSsoSession());

    expect(completions).toEqual([PROFILE.email]);
    expect(fetchUserProfileAPIMock).toHaveBeenCalledTimes(1);
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
    expect(store.getState().auth.user?.id).toBe(PROFILE.id);
  });

  it('adopts the SDK atomically committed token and enters authenticated state', async () => {
    const token = tokenFor(PROFILE.id);
    resumeCentralSessionMock.mockImplementation(async (options) => {
      await options?.beforeCommit?.({ user: PROFILE });
      return { status: 'resumed', user: PROFILE };
    });
    getStoredAccessTokenMock.mockReturnValue(token);
    const store = configureStore({ reducer: { auth: authReducer } });

    const result = await store.dispatch(resumeSsoSession()).unwrap();

    expect(result).toBe('resumed');
    expect(store.getState().auth.isAuthenticated).toBe(true);
    expect(store.getState().auth.user?.id).toBe(PROFILE.id);
    expect(store.getState().auth.token).toBe(token);
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
  });

  it('leaves the host unauthenticated when the central session is absent', async () => {
    resumeCentralSessionMock.mockResolvedValue({ status: 'no_session' });
    const store = configureStore({ reducer: { auth: authReducer } });

    const result = await store.dispatch(resumeSsoSession()).unwrap();

    expect(result).toBe('no_session');
    expect(store.getState().auth.isAuthenticated).toBe(false);
    expect(store.getState().auth.token).toBeNull();
  });

  it('adopts a sibling-tab local_session through the same cache-clearing boundary', async () => {
    const token = tokenFor(PROFILE.id);
    resumeCentralSessionMock.mockResolvedValue({ status: 'local_session' });
    getStoredAccessTokenMock.mockReturnValue(token);
    const store = configureStore({ reducer: { auth: authReducer } });

    const result = await store.dispatch(resumeSsoSession()).unwrap();

    expect(result).toBe('local_session');
    expect(store.getState().auth.isAuthenticated).toBe(true);
    expect(store.getState().auth.user?.id).toBe(PROFILE.id);
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
  });

  it('blocks a transition when central resume rejects after beforeCommit instead of spinning forever', async () => {
    resumeCentralSessionMock.mockImplementation(async (options) => {
      await options?.beforeCommit?.({ user: PROFILE });
      throw new Error('central resume failed');
    });
    const store = configureStore({ reducer: { auth: authReducer } });

    await expect(store.dispatch(resumeSsoSession()).unwrap()).rejects.toThrow(
      'central resume failed',
    );

    expect(getAuthSessionTransitionState()).toBe('blocked');
    expect(store.getState().auth.accountSwitchStatus).toBe('blocked');
    expect(store.getState().auth.accountSwitchError).toBe('central resume failed');
  });

  it('settles Redux immediately when the SDK definitively clears its stored session', async () => {
    const token = tokenFor(PROFILE.id);
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(setToken(token));
    getStoredAccessTokenMock.mockReturnValue(null);

    await store.dispatch(settleSdkUnauthenticatedSession());

    expect(store.getState().auth.isAuthenticated).toBe(false);
    expect(store.getState().auth.token).toBeNull();
    expect(store.getState().auth.accountSwitchStatus).toBe('stable');
    expect(clearFusionProfileStorageMock).toHaveBeenCalledTimes(1);
  });

  it('ignores a stale SDK unauthenticated notification when a newer stored session already exists', async () => {
    const token = tokenFor(PROFILE.id);
    getStoredAccessTokenMock.mockReturnValue(token);
    const store = configureStore({ reducer: { auth: authReducer } });
    store.dispatch(setToken(token));

    await store.dispatch(settleSdkUnauthenticatedSession());

    expect(store.getState().auth.isAuthenticated).toBe(true);
    expect(store.getState().auth.token).toBe(token);
    expect(clearFusionProfileStorageMock).not.toHaveBeenCalled();
  });
});
