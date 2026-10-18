import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const {
  currentAuthState,
  appDispatchMock,
  reduxDispatchMock,
  useAppDispatchMock,
  useAppSelectorMock,
  initializeModelsMock,
  setModelsLoadStatusMock,
  updateModelsMock,
  updateProvidersMock,
  checkUserStateMock,
  fetchUserProfileMock,
  checkLivenessMock,
  resumeSsoSessionMock,
  adoptCommittedSsoSessionMock,
  settleSdkUnauthenticatedSessionMock,
  restoreLocalSessionMock,
  resolveSessionMock,
  setGlobalToastMock,
  canAutoResumeSessionMock,
  subscribeSsoStateMock,
  getStoredAccessTokenMock,
  beginAuthSessionTransitionMock,
  isAuthSessionTransitionErrorMock,
  waitForAuthSessionStableMock,
  routerReplaceMock,
  toastSuccessMock,
} = vi.hoisted(() => ({
  currentAuthState: {
    isAuthenticated: false,
    status: 'idle',
    accountSwitchStatus: 'stable',
    accountSwitchError: null,
    switchedAccountEmail: null,
    sessionResolved: false,
  } as {
    isAuthenticated: boolean;
    status: 'idle' | 'loading' | 'succeeded' | 'failed';
    accountSwitchStatus: 'stable' | 'synchronizing' | 'blocked';
    accountSwitchError: string | null;
    switchedAccountEmail: string | null;
    sessionResolved: boolean;
  },
  appDispatchMock: vi.fn(),
  reduxDispatchMock: vi.fn(),
  useAppDispatchMock: vi.fn(),
  useAppSelectorMock: vi.fn(),
  initializeModelsMock: vi.fn(),
  setModelsLoadStatusMock: vi.fn((status: unknown) => ({ type: 'models/setModelsLoadStatus', payload: status })),
  updateModelsMock: vi.fn((models: unknown) => ({ type: 'models/updateModels', payload: models })),
  updateProvidersMock: vi.fn((providers: unknown) => ({ type: 'models/updateProviders', payload: providers })),
  checkUserStateMock: vi.fn(() => ({ type: 'auth/checkUserState' })),
  fetchUserProfileMock: vi.fn(() => ({ type: 'auth/fetchUserProfile' })),
  checkLivenessMock: vi.fn(() => ({ type: 'auth/checkLiveness' })),
  resumeSsoSessionMock: vi.fn(() => ({ type: 'auth/resumeSsoSession' })),
  adoptCommittedSsoSessionMock: vi.fn((payload: unknown) => ({
    type: 'auth/adoptCommittedSsoSession',
    payload,
  })),
  settleSdkUnauthenticatedSessionMock: vi.fn(() => ({
    type: 'auth/settleSdkUnauthenticatedSession',
  })),
  restoreLocalSessionMock: vi.fn(() => ({
    type: 'auth/restoreLocalSession',
  })),
  resolveSessionMock: vi.fn(() => ({ type: 'auth/resolveSession' })),
  setGlobalToastMock: vi.fn(),
  canAutoResumeSessionMock: vi.fn(() => true),
  subscribeSsoStateMock: vi.fn(() => vi.fn()),
  getStoredAccessTokenMock: vi.fn<() => string | null>(() => null),
  beginAuthSessionTransitionMock: vi.fn(),
  isAuthSessionTransitionErrorMock: vi.fn(
    (error: unknown) => (
      typeof error === 'object'
      && error !== null
      && 'code' in error
      && error.code === 'AUTH_SESSION_TRANSITION'
    ),
  ),
  waitForAuthSessionStableMock: vi.fn(() => Promise.resolve()),
  routerReplaceMock: vi.fn(),
  toastSuccessMock: vi.fn(),
}));

vi.mock('next/dynamic', () => ({
  default: () => () => null,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: routerReplaceMock }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: useAppDispatchMock,
  useAppSelector: useAppSelectorMock,
}));

vi.mock('react-redux', async () => {
  const actual = await vi.importActual<typeof import('react-redux')>('react-redux');
  return {
    ...actual,
    useDispatch: () => reduxDispatchMock,
  };
});

vi.mock('@/lib/config/modelConfig', () => ({
  initializeModels: initializeModelsMock,
}));

vi.mock('@/redux/slices/modelsSlice', () => ({
  setModelsLoadStatus: setModelsLoadStatusMock,
  updateModels: updateModelsMock,
  updateProviders: updateProvidersMock,
}));

vi.mock('@/redux/slices/guestSlice', () => ({
  bootstrapGuestSession: () => ({ type: 'guest/bootstrap' }),
  isGuestSessionReady: (action: { guestReady?: boolean } | undefined) => action?.guestReady === true,
  refreshEntitlements: () => ({ type: 'guest/refreshEntitlements' }),
  entitlementsCleared: () => ({ type: 'guest/entitlementsCleared' }),
  guestSessionInvalidated: () => ({ type: 'guest/guestSessionInvalidated' }),
}));

vi.mock('@/redux/slices/authSlice', () => ({
  checkUserState: checkUserStateMock,
  fetchUserProfile: fetchUserProfileMock,
  checkLiveness: checkLivenessMock,
  resumeSsoSession: resumeSsoSessionMock,
  adoptCommittedSsoSession: adoptCommittedSsoSessionMock,
  settleSdkUnauthenticatedSession: settleSdkUnauthenticatedSessionMock,
  restoreLocalSession: restoreLocalSessionMock,
  resolveSession: resolveSessionMock,
  setToken: vi.fn(),
}));

vi.mock('@/lib/auth/sso-probe', () => ({
  canAutoResumeSession: canAutoResumeSessionMock,
}));

vi.mock('@/lib/auth/authService', () => ({
  subscribeSsoState: subscribeSsoStateMock,
  getStoredAccessToken: getStoredAccessTokenMock,
}));

vi.mock('@/lib/auth/sessionTransition', () => ({
  beginAuthSessionTransition: beginAuthSessionTransitionMock,
  isAuthSessionTransitionError: isAuthSessionTransitionErrorMock,
  waitForAuthSessionStable: waitForAuthSessionStableMock,
}));

vi.mock('@/components/ui/toast', () => ({
  ToastProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
  useToast: () => ({ show: vi.fn() }),
  setGlobalToast: setGlobalToastMock,
  toast: { success: toastSuccessMock },
}));

vi.mock('@/components/auth/LoginDialog', () => ({
  LoginDialog: ({ open }: { open: boolean }) =>
    React.createElement('div', {
      'data-testid': 'login-dialog',
      'data-open': open ? 'true' : 'false',
    }),
}));

vi.mock('@/components/settings/SettingsDialog', () => ({
  SettingsDialog: () =>
    React.createElement('div', {
      'data-testid': 'settings-dialog',
    }),
}));

import ClientLayout from './ClientLayout';

describe('ClientLayout', () => {
  beforeEach(() => {
    currentAuthState.isAuthenticated = false;
    currentAuthState.status = 'idle';
    currentAuthState.accountSwitchStatus = 'stable';
    currentAuthState.accountSwitchError = null;
    currentAuthState.switchedAccountEmail = null;
    currentAuthState.sessionResolved = false;
    localStorage.clear();
    appDispatchMock.mockReset();
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      return action;
    });
    reduxDispatchMock.mockReset();
    useAppDispatchMock.mockReturnValue(appDispatchMock);
    useAppSelectorMock.mockImplementation(selector => selector({ auth: currentAuthState }));
    initializeModelsMock.mockReset();
    initializeModelsMock.mockResolvedValue({
      models: [{ id: 'model-1' }],
      providers: [{ id: 'qwen', name: '通义千问', order: 1 }],
    });
    setModelsLoadStatusMock.mockClear();
    updateModelsMock.mockClear();
    updateProvidersMock.mockClear();
    checkUserStateMock.mockClear();
    fetchUserProfileMock.mockClear();
    checkLivenessMock.mockClear();
    resumeSsoSessionMock.mockClear();
    adoptCommittedSsoSessionMock.mockClear();
    settleSdkUnauthenticatedSessionMock.mockClear();
    restoreLocalSessionMock.mockClear();
    resolveSessionMock.mockClear();
    setGlobalToastMock.mockClear();
    canAutoResumeSessionMock.mockReset();
    canAutoResumeSessionMock.mockReturnValue(true);
    subscribeSsoStateMock.mockClear();
    getStoredAccessTokenMock.mockReset();
    getStoredAccessTokenMock.mockReturnValue(null);
    beginAuthSessionTransitionMock.mockClear();
    isAuthSessionTransitionErrorMock.mockClear();
    waitForAuthSessionStableMock.mockReset();
    waitForAuthSessionStableMock.mockResolvedValue();
    routerReplaceMock.mockClear();
    toastSuccessMock.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('refreshes user profile when authenticated state is stale', async () => {
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'idle';

    render(
      React.createElement(
        ClientLayout,
        null,
        React.createElement('div', null, 'child')
      )
    );

    await waitFor(() => {
      expect(checkUserStateMock).toHaveBeenCalledTimes(1);
      expect(fetchUserProfileMock).toHaveBeenCalledTimes(1);
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkUserState' });
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/fetchUserProfile' });
    });
  });

  it('does not repeat auth checks or profile fetches when profile loading completes', async () => {
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'idle';

    const createLayout = () =>
      React.createElement(
        ClientLayout,
        null,
        React.createElement('div', null, 'child')
      );
    const { rerender } = render(createLayout());

    await waitFor(() => {
      expect(checkUserStateMock).toHaveBeenCalledTimes(1);
      expect(fetchUserProfileMock).toHaveBeenCalledTimes(1);
    });

    currentAuthState.status = 'loading';
    rerender(createLayout());
    currentAuthState.status = 'succeeded';
    rerender(createLayout());

    await waitFor(() => {
      expect(checkUserStateMock).toHaveBeenCalledTimes(1);
      expect(fetchUserProfileMock).toHaveBeenCalledTimes(1);
    });
  });

  it('loads models and opens login dialog for unauthenticated users after delay', async () => {
    render(
      React.createElement(
        ClientLayout,
        null,
        React.createElement('div', null, 'child')
      )
    );

    await waitFor(() => {
      expect(initializeModelsMock).toHaveBeenCalledTimes(1);
      expect(setModelsLoadStatusMock).toHaveBeenCalledWith('loading');
      expect(updateModelsMock).toHaveBeenCalledWith([{ id: 'model-1' }]);
      expect(reduxDispatchMock).toHaveBeenCalledWith({
        type: 'models/updateModels',
        payload: [{ id: 'model-1' }],
      });
    });

    expect(screen.getByTestId('login-dialog').getAttribute('data-open')).toBe('false');

    await new Promise(resolve => setTimeout(resolve, 1100));

    await waitFor(() => {
      expect(screen.getByTestId('login-dialog').getAttribute('data-open')).toBe('true');
    });
  });

  it('游客模式可用时以游客身份进入，不弹登录框', async () => {
    appDispatchMock.mockImplementation((action: { type?: string } | undefined) => {
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      return action?.type === 'guest/bootstrap' ? { guestReady: true } : action;
    });

    render(
      React.createElement(
        ClientLayout,
        null,
        React.createElement('div', null, 'child')
      )
    );

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'guest/bootstrap' });
    });
    await new Promise(resolve => setTimeout(resolve, 1600));
    expect(screen.getByTestId('login-dialog').getAttribute('data-open')).toBe('false');
  });

  it('模型目录初始化失败时记录失败状态', async () => {
    initializeModelsMock.mockRejectedValueOnce(new Error('模型目录不可达'));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(
      React.createElement(
        ClientLayout,
        null,
        React.createElement('div', null, 'child')
      )
    );

    await waitFor(() => {
      expect(setModelsLoadStatusMock).toHaveBeenNthCalledWith(1, 'loading');
      expect(setModelsLoadStatusMock).toHaveBeenNthCalledWith(2, 'failed');
    });
    expect(updateModelsMock).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith(
      'Failed to initialize models:',
      expect.objectContaining({ message: '模型目录不可达' }),
    );

    consoleError.mockRestore();
  });

  it('waits for a stable auth session and retries model initialization without logging a transition error', async () => {
    const transitionError = Object.assign(new Error('账户正在同步'), {
      code: 'AUTH_SESSION_TRANSITION',
    });
    let releaseStable!: () => void;
    initializeModelsMock
      .mockRejectedValueOnce(transitionError)
      .mockResolvedValueOnce({
        models: [{ id: 'model-after-switch' }],
        providers: [{ id: 'qwen', name: '通义千问', order: 1 }],
      });
    waitForAuthSessionStableMock.mockReturnValue(new Promise<void>((resolve) => {
      releaseStable = resolve;
    }));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(initializeModelsMock).toHaveBeenCalledTimes(1);
      expect(waitForAuthSessionStableMock).toHaveBeenCalledTimes(1);
    });
    expect(updateModelsMock).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();

    releaseStable();

    await waitFor(() => {
      expect(initializeModelsMock).toHaveBeenCalledTimes(2);
      expect(updateModelsMock).toHaveBeenCalledWith([{ id: 'model-after-switch' }]);
    });
    expect(consoleError).not.toHaveBeenCalled();
  });

  it('runs a read-only liveness probe on window focus when authenticated (SLO)', async () => {
    // 跨应用单点登出：别处登出后本标签页令牌仍密码学有效，重新聚焦时做一次【只读】存活探测。
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';

    const now = vi.spyOn(Date, 'now').mockReturnValue(10_000);
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(checkUserStateMock).toHaveBeenCalled();
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
    });
    appDispatchMock.mockClear(); // 隔离 focus 触发的派发
    now.mockReturnValue(14_000);

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
    });
  });

  it('runs the liveness probe when the tab becomes visible again (visibilitychange → visible)', async () => {
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';

    const now = vi.spyOn(Date, 'now').mockReturnValue(10_000);
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' }));
    appDispatchMock.mockClear();
    now.mockReturnValue(14_000);

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
    });
  });

  it('coalesces focus + visibilitychange within the 3s debounce into a single probe', async () => {
    // 切回标签页常同时触发 focus + visibilitychange；去抖刻意把它们合并成一次探测，避免双打 /api/auth/me。
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';

    const now = vi.spyOn(Date, 'now').mockReturnValue(10_000);
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' }));
    appDispatchMock.mockClear();
    now.mockReturnValue(14_000);

    window.dispatchEvent(new Event('focus'));
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
    });
    const probeDispatches = appDispatchMock.mock.calls.filter(
      (c) => c[0] && c[0].type === 'auth/checkLiveness'
    );
    expect(probeDispatches).toHaveLength(1);
  });

  it('blocks and adopts the SDK session committed by a sibling tab only once for duplicate authenticated notifications', async () => {
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';
    let listener: ((state: {
      status: 'synchronizing' | 'authenticated';
      user: { email?: string } | null;
    }) => void) | null = null;
    (subscribeSsoStateMock as unknown as {
      mockImplementation: (implementation: (next: typeof listener) => () => void) => void;
    }).mockImplementation((next: typeof listener) => {
      listener = next;
      return vi.fn();
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(subscribeSsoStateMock).toHaveBeenCalledTimes(1));
    act(() => listener?.({ status: 'synchronizing', user: null }));
    expect(beginAuthSessionTransitionMock).toHaveBeenCalledTimes(1);
    expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/accountSessionSwitchStarted' });

    act(() => listener?.({ status: 'authenticated', user: { email: 'b@example.com' } }));
    act(() => listener?.({ status: 'authenticated', user: { email: 'b@example.com' } }));
    expect(adoptCommittedSsoSessionMock).toHaveBeenCalledWith({ email: 'b@example.com' });
    expect(adoptCommittedSsoSessionMock).toHaveBeenCalledTimes(1);
    expect(appDispatchMock).toHaveBeenCalledWith({
      type: 'auth/adoptCommittedSsoSession',
      payload: { email: 'b@example.com' },
    });
  });

  it('settles the host Redux session when the SDK reports a definitive unauthenticated state', async () => {
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';
    let listener: ((state: {
      status: 'unauthenticated';
      user: null;
    }) => void) | null = null;
    (subscribeSsoStateMock as unknown as {
      mockImplementation: (implementation: (next: typeof listener) => () => void) => void;
    }).mockImplementation((next: typeof listener) => {
      listener = next;
      return vi.fn();
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(subscribeSsoStateMock).toHaveBeenCalledTimes(1));

    act(() => listener?.({ status: 'unauthenticated', user: null }));

    expect(settleSdkUnauthenticatedSessionMock).toHaveBeenCalledTimes(1);
    expect(appDispatchMock).toHaveBeenCalledWith({
      type: 'auth/settleSdkUnauthenticatedSession',
    });
  });

  it('resumes the central session on focus when unauthenticated', async () => {
    currentAuthState.isAuthenticated = false;

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));
    appDispatchMock.mockClear();

    window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(appDispatchMock).not.toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
    expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/resumeSsoSession' });
  });

  it('does NOT resume after explicit logout or on an exempt route', async () => {
    currentAuthState.isAuthenticated = false;
    canAutoResumeSessionMock.mockReturnValue(false);

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    appDispatchMock.mockClear();

    window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(canAutoResumeSessionMock).toHaveBeenCalledWith(
      window.location.pathname + window.location.search
    );
    expect(appDispatchMock).not.toHaveBeenCalledWith({ type: 'auth/resumeSsoSession' });
  });

  it('coalesces unauthenticated focus + visibilitychange into one resume', async () => {
    currentAuthState.isAuthenticated = false;
    const now = vi.spyOn(Date, 'now').mockReturnValue(10_000);

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));
    appDispatchMock.mockClear();
    now.mockReturnValue(14_000);

    window.dispatchEvent(new Event('focus'));
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    const resumeDispatches = appDispatchMock.mock.calls.filter(
      (call) => call[0] && call[0].type === 'auth/resumeSsoSession'
    );
    expect(resumeDispatches).toHaveLength(1);
  });

  it('runs the low-frequency liveness probe on the 5-minute backstop timer when authenticated', async () => {
    // 兜底盲区：长时间聚焦却空闲、不切标签也不发受保护请求（纯 SSE / 阅读态）的页面，
    // 靠定时只读探测感知别处登出。用假定时器推进 5 分钟，断言 checkLiveness 被派发。
    vi.useFakeTimers();
    currentAuthState.isAuthenticated = true;
    currentAuthState.status = 'succeeded';

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    // 清掉挂载期（checkUserState 等）的派发，只观察定时器触发的那一次。
    await vi.advanceTimersByTimeAsync(0);
    appDispatchMock.mockClear();

    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);

    expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/checkLiveness' });
  });

  it('runs the low-frequency session resume fallback when unauthenticated', async () => {
    vi.useFakeTimers();
    currentAuthState.isAuthenticated = false;

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await vi.advanceTimersByTimeAsync(0);
    appDispatchMock.mockClear();

    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);

    expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/resumeSsoSession' });
  });

  it('restores the central session on first load without navigating away', async () => {
    // 没有本地 token：首屏只走 POST /auth/session/resume，不做顶层 prompt=none 跳转。
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('resumed') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    expect(canAutoResumeSessionMock).toHaveBeenCalledWith(
      window.location.pathname + window.location.search
    );
    await new Promise(resolve => setTimeout(resolve, 1700));

    expect(resolveSessionMock).not.toHaveBeenCalled();
    expect(screen.getByTestId('login-dialog').getAttribute('data-open')).toBe('false');
  });

  it('resolves the session (reveals the login terminal) when the central resume reports no session', async () => {
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/resolveSession' });
    });
    expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1);
  });

  it('does NOT resolve the session while the central resume is in flight (avoids the login-button flash)', async () => {
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => new Promise(() => {}) };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    await new Promise(resolve => setTimeout(resolve, 50));

    expect(appDispatchMock).not.toHaveBeenCalledWith({ type: 'auth/resolveSession' });
  });

  it('reveals the login terminal without resuming after explicit logout', async () => {
    canAutoResumeSessionMock.mockReturnValue(false);

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(appDispatchMock).toHaveBeenCalledWith({ type: 'auth/resolveSession' });
    });
    expect(resumeSsoSessionMock).not.toHaveBeenCalled();
  });

  it('restores a preserved local refresh session before attempting central resume', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('restored') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    });
    expect(resumeSsoSessionMock).not.toHaveBeenCalled();
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('uses no-navigation central resume after an unknown refresh outcome', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('central_recovery_required') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('resumed') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('stays inside Fusion when bounded central recovery also remains unavailable', async () => {
    vi.useFakeTimers();
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('central_recovery_required') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.reject(new TypeError('auth unavailable')) };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(14_000);
    });

    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    expect(resumeSsoSessionMock).toHaveBeenCalledTimes(4);
    expect(resolveSessionMock).toHaveBeenCalledTimes(1);
  });

  it('does not resolve or start central SSO when the local refresh fails transiently', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('transient_failure') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    });
    expect(resumeSsoSessionMock).not.toHaveBeenCalled();
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('automatically retries a transient local refresh while auth-service restarts', async () => {
    vi.useFakeTimers();
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    let attempts = 0;
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        attempts += 1;
        return {
          unwrap: () => Promise.resolve(
            attempts === 1 ? 'transient_failure' : 'restored',
          ),
        };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(2);
    expect(resumeSsoSessionMock).not.toHaveBeenCalled();
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('falls back to central SSO after all transient local retries are exhausted', async () => {
    vi.useFakeTimers();
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('transient_failure') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('resumed') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(14_000);
    });

    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(4);
    expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1);
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('does not start a duplicate focus recovery while the initial transient retry is scheduled', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('transient_failure') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1));
    restoreLocalSessionMock.mockClear();
    appDispatchMock.mockClear();

    window.dispatchEvent(new Event('focus'));

    await new Promise(resolve => setTimeout(resolve, 50));
    expect(restoreLocalSessionMock).not.toHaveBeenCalled();
    expect(resumeSsoSessionMock).not.toHaveBeenCalled();
  });

  it('falls through from a definitive focus refresh miss to central SSO in the same probe', async () => {
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));

    getStoredAccessTokenMock.mockReturnValue('expired-token');
    restoreLocalSessionMock.mockClear();
    resumeSsoSessionMock.mockClear();
    resolveSessionMock.mockClear();
    appDispatchMock.mockClear();
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      return action;
    });

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    expect(resolveSessionMock).toHaveBeenCalledTimes(1);
  });

  it('falls through from a transient focus refresh failure to central SSO in the same probe', async () => {
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));

    getStoredAccessTokenMock.mockReturnValue('expired-token');
    restoreLocalSessionMock.mockClear();
    resumeSsoSessionMock.mockClear();
    resolveSessionMock.mockClear();
    appDispatchMock.mockClear();
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('transient_failure') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      return action;
    });

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    expect(resolveSessionMock).toHaveBeenCalledTimes(1);
  });

  it('reveals a retryable login terminal when local and central focus recovery both fail', async () => {
    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));

    getStoredAccessTokenMock.mockReturnValue('expired-token');
    resumeSsoSessionMock.mockClear();
    resolveSessionMock.mockClear();
    appDispatchMock.mockClear();
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('transient_failure') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.reject(new TypeError('auth unavailable')) };
      }
      return action;
    });

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => expect(resolveSessionMock).toHaveBeenCalledTimes(1));
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);
    expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1);
  });

  it('falls back to central resume only after the SDK definitively reports no local session', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => Promise.resolve('no_session') };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => Promise.resolve('resumed') };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );

    await waitFor(() => {
      expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1);
    });
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it('keeps the session unresolved while a definitive local miss is falling back to central resume', async () => {
    getStoredAccessTokenMock.mockReturnValue('expired-token');
    let listener: ((state: {
      status: 'unauthenticated';
      user: null;
    }) => void) | null = null;
    let finishRecovery!: (result: 'no_session') => void;
    const recovery = new Promise<'no_session'>((resolve) => {
      finishRecovery = resolve;
    });
    (subscribeSsoStateMock as unknown as {
      mockImplementation: (implementation: (next: typeof listener) => () => void) => void;
    }).mockImplementation((next: typeof listener) => {
      listener = next;
      return vi.fn();
    });
    appDispatchMock.mockImplementation((action) => {
      if (action?.type === 'auth/restoreLocalSession') {
        return { unwrap: () => recovery };
      }
      if (action?.type === 'auth/resumeSsoSession') {
        return { unwrap: () => new Promise(() => {}) };
      }
      return action;
    });

    render(
      React.createElement(ClientLayout, null, React.createElement('div', null, 'child'))
    );
    await waitFor(() => expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1));

    window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(restoreLocalSessionMock).toHaveBeenCalledTimes(1);

    act(() => listener?.({ status: 'unauthenticated', user: null }));
    expect(settleSdkUnauthenticatedSessionMock).not.toHaveBeenCalled();

    finishRecovery('no_session');
    await waitFor(() => expect(resumeSsoSessionMock).toHaveBeenCalledTimes(1));
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });
});
