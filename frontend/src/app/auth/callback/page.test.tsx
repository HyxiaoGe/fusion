import React, { act } from 'react';
import { render, waitFor } from '@testing-library/react';
import { hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { dispatchMock, replaceMock, completeLoginMock, toastMock, unwrapMock, notifyLoginSucceededMock } = vi.hoisted(() => ({
  dispatchMock: vi.fn(),
  replaceMock: vi.fn(),
  completeLoginMock: vi.fn(() => ({ type: 'auth/completeLogin' })),
  toastMock: vi.fn(),
  unwrapMock: vi.fn(),
  notifyLoginSucceededMock: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => dispatchMock,
}));

vi.mock('@/redux/slices/authSlice', () => ({
  completeLogin: completeLoginMock,
}));

vi.mock('@/lib/auth/loginNotice', () => ({
  notifyLoginSucceeded: notifyLoginSucceededMock,
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));

import AuthCallbackPage from './page';

describe('AuthCallbackPage (SDK callback)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // dispatch(completeLogin()) returns a thenable with .unwrap() (RTK thunk dispatch)
    dispatchMock.mockReturnValue({ unwrap: unwrapMock });
  });

  it('dispatches completeLogin and redirects to the resolved path on success', async () => {
    unwrapMock.mockResolvedValue({ redirectPath: '/chat/9' });

    render(React.createElement(AuthCallbackPage));

    await waitFor(() => {
      expect(completeLoginMock).toHaveBeenCalledTimes(1);
      expect(dispatchMock).toHaveBeenCalledWith({ type: 'auth/completeLogin' });
      expect(replaceMock).toHaveBeenCalledWith('/chat/9');
      expect(toastMock).not.toHaveBeenCalled();
    });
  });

  it('交互式登录（带 ?code）成功后提示一次登录成功', async () => {
    window.history.pushState({}, '', '/auth/callback?code=abc&state=s');
    unwrapMock.mockResolvedValue({ redirectPath: '/chat/9', signedInEmail: 'a@b.com' });

    render(React.createElement(AuthCallbackPage));

    await waitFor(() => {
      expect(notifyLoginSucceededMock).toHaveBeenCalledTimes(1);
      expect(notifyLoginSucceededMock).toHaveBeenCalledWith('a@b.com');
      expect(replaceMock).toHaveBeenCalledWith('/chat/9');
    });
  });

  it('登出回跳（无 ?code）即使恢复了已有会话也不提示登录成功', async () => {
    window.history.pushState({}, '', '/auth/callback');
    unwrapMock.mockResolvedValue({ redirectPath: '/', signedInEmail: 'a@b.com' });

    render(React.createElement(AuthCallbackPage));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith('/'));
    expect(notifyLoginSucceededMock).not.toHaveBeenCalled();
  });

  it('toasts an error and soft-lands at "/" when the callback fails', async () => {
    unwrapMock.mockRejectedValue('state mismatch');

    render(React.createElement(AuthCallbackPage));

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledTimes(1);
      expect(replaceMock).toHaveBeenCalledWith('/');
    });
  });

  // #3 root fix: /auth/callback is the shared landing for an interactive login AND for a
  // logout bounce (Single Logout 302s back here with no ?code) AND a silent-probe transit.
  // It must only show "正在完成授权" when there is actually an auth code to exchange (a genuine
  // user-initiated login); a logout/probe transit must render a neutral loader, never claim
  // authorization is "完成中".
  const AUTHORIZING = '正在完成授权，请稍候...';

  it('hydrates an interactive callback from the neutral server frame without a mismatch', async () => {
    window.history.pushState({}, '', '/auth/callback');
    unwrapMock.mockReturnValue(new Promise(() => {}));
    const container = document.createElement('div');
    container.innerHTML = renderToString(React.createElement(AuthCallbackPage));

    window.history.pushState({}, '', '/auth/callback?code=abc&state=s');
    const recoverableErrors: unknown[] = [];
    let root: ReturnType<typeof hydrateRoot> | undefined;

    await act(async () => {
      root = hydrateRoot(container, React.createElement(AuthCallbackPage), {
        onRecoverableError: (error) => recoverableErrors.push(error),
      });
      await Promise.resolve();
    });

    expect(recoverableErrors).toEqual([]);
    expect(container.textContent).toContain(AUTHORIZING);

    await act(async () => {
      root?.unmount();
    });
  });

  it('interactive login (?code present): shows the "正在完成授权" copy', () => {
    window.history.pushState({}, '', '/auth/callback?code=abc&state=s');
    unwrapMock.mockReturnValue(new Promise(() => {})); // pending: stay on the screen
    const { getByText } = render(React.createElement(AuthCallbackPage));
    expect(getByText(AUTHORIZING)).toBeTruthy();
  });

  it('logout bounce (no ?code): renders a neutral loader, NOT "正在完成授权"', () => {
    window.history.pushState({}, '', '/auth/callback'); // post-logout 302 lands here with no code
    unwrapMock.mockReturnValue(new Promise(() => {})); // pending: stay on the screen
    const { queryByText } = render(React.createElement(AuthCallbackPage));
    expect(queryByText(AUTHORIZING)).toBeNull();
  });
});
