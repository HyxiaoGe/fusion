import { configureStore } from '@reduxjs/toolkit';
import { render } from '@testing-library/react';
import { act } from 'react';
import { Provider } from 'react-redux';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const analyticsMock = vi.hoisted(() => ({
  initAnalytics: vi.fn(),
  identifyUser: vi.fn(),
  resetAnalyticsIdentity: vi.fn(),
  registerAnalyticsContext: vi.fn(),
}));
vi.mock('@/lib/analytics/analytics', () => analyticsMock);
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN' } }),
}));

import AnalyticsBootstrap from './AnalyticsBootstrap';

type AuthSlice = { isAuthenticated: boolean; user: { id: string; is_superuser: boolean; loginProvider?: string | null } | null };
type GuestSlice = { status: 'idle' | 'ready' };

function makeStore(auth: AuthSlice, guest: GuestSlice = { status: 'idle' }) {
  return configureStore({
    reducer: {
      auth: (state: AuthSlice = auth, action: { type: string; payload?: AuthSlice }) =>
        action.type === 'test/auth' && action.payload ? action.payload : state,
      guest: (state: GuestSlice = guest, action: { type: string; payload?: GuestSlice }) =>
        action.type === 'test/guest' && action.payload ? action.payload : state,
    },
  });
}

describe('AnalyticsBootstrap', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.clearAllMocks());

  it('挂载即初始化；未登录不 identify，公共属性带是否游客与语言', () => {
    const store = makeStore({ isAuthenticated: false, user: null }, { status: 'ready' });
    render(<Provider store={store}><AnalyticsBootstrap /></Provider>);
    expect(analyticsMock.initAnalytics).toHaveBeenCalledTimes(1);
    expect(analyticsMock.identifyUser).not.toHaveBeenCalled();
    expect(analyticsMock.resetAnalyticsIdentity).not.toHaveBeenCalled();
    expect(analyticsMock.registerAnalyticsContext).toHaveBeenLastCalledWith({ is_guest: true, locale: 'zh-CN' });
  });

  it('登录后 identify 一次，同一用户重渲染不重复；退出后 reset 一次', () => {
    const store = makeStore({ isAuthenticated: false, user: null });
    render(<Provider store={store}><AnalyticsBootstrap /></Provider>);
    act(() => {
      store.dispatch({ type: 'test/auth', payload: { isAuthenticated: true, user: { id: 'u1', is_superuser: true, loginProvider: 'github' } } });
    });
    act(() => {
      store.dispatch({ type: 'test/auth', payload: { isAuthenticated: true, user: { id: 'u1', is_superuser: true, loginProvider: 'github' } } });
    });
    expect(analyticsMock.identifyUser).toHaveBeenCalledTimes(1);
    expect(analyticsMock.identifyUser).toHaveBeenCalledWith({ id: 'u1', is_superuser: true, loginProvider: 'github' });
    act(() => {
      store.dispatch({ type: 'test/auth', payload: { isAuthenticated: false, user: null } });
    });
    expect(analyticsMock.resetAnalyticsIdentity).toHaveBeenCalledTimes(1);
    expect(analyticsMock.registerAnalyticsContext).toHaveBeenLastCalledWith({ is_guest: false, locale: 'zh-CN' });
  });

  it('首屏尚未恢复会话（从未登录过）时不 reset 匿名身份', () => {
    const store = makeStore({ isAuthenticated: false, user: null });
    render(<Provider store={store}><AnalyticsBootstrap /></Provider>);
    act(() => {
      store.dispatch({ type: 'test/guest', payload: { status: 'ready' } });
    });
    expect(analyticsMock.resetAnalyticsIdentity).not.toHaveBeenCalled();
  });
});
