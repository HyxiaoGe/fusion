import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const posthogMock = vi.hoisted(() => ({
  init: vi.fn(),
  capture: vi.fn(),
  identify: vi.fn(),
  reset: vi.fn(),
  register: vi.fn(),
}));

vi.mock('posthog-js', () => ({ default: posthogMock }));

import {
  identifyUser,
  initAnalytics,
  isAnalyticsEnabled,
  registerAnalyticsContext,
  resetAnalyticsForTests,
  resetAnalyticsIdentity,
  track,
  uiHostFromIngestHost,
} from './analytics';

describe('analytics 封装', () => {
  beforeEach(() => {
    resetAnalyticsForTests();
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_KEY', 'phc_test');
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_HOST', 'https://eu.i.posthog.com');
    vi.stubEnv('NEXT_PUBLIC_BUILD_VERSION', 'build-1');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.clearAllMocks();
  });

  it('没配 key 时不初始化，所有调用都是空操作', () => {
    vi.stubEnv('NEXT_PUBLIC_POSTHOG_KEY', '');
    expect(initAnalytics()).toBe(false);
    expect(isAnalyticsEnabled()).toBe(false);
    track('logout');
    identifyUser({ id: 'u1', is_superuser: false });
    registerAnalyticsContext({ is_guest: true, locale: 'zh-CN' });
    resetAnalyticsIdentity();
    expect(posthogMock.init).not.toHaveBeenCalled();
    expect(posthogMock.capture).not.toHaveBeenCalled();
    expect(posthogMock.identify).not.toHaveBeenCalled();
  });

  it('初始化走同源 /ingest 反代、按 history 记页面浏览、只给已识别用户建档，且只初始化一次', () => {
    expect(initAnalytics()).toBe(true);
    expect(initAnalytics()).toBe(true);
    expect(posthogMock.init).toHaveBeenCalledTimes(1);
    const [key, config] = posthogMock.init.mock.calls[0];
    expect(key).toBe('phc_test');
    expect(config.api_host).toBe(`${window.location.origin}/ingest`);
    expect(config.ui_host).toBe('https://eu.posthog.com');
    expect(config.capture_pageview).toBe('history_change');
    expect(config.person_profiles).toBe('identified_only');
    expect(config.session_recording).toEqual({ maskAllInputs: true });
    expect(config.capture_exceptions).toBe(true);
    expect(posthogMock.register).toHaveBeenCalledWith({ app_version: 'build-1' });
  });

  it('track 把事件与属性原样交给 PostHog，无属性事件送空对象', () => {
    initAnalytics();
    track('login_started', { method: 'sso', provider: 'github', source: 'header' });
    track('logout');
    expect(posthogMock.capture).toHaveBeenNthCalledWith(1, 'login_started', {
      method: 'sso',
      provider: 'github',
      source: 'header',
    });
    expect(posthogMock.capture).toHaveBeenNthCalledWith(2, 'logout', {});
  });

  it('identify 只带角色与登录方式，不带邮箱；reset 与公共属性直接透传', () => {
    initAnalytics();
    identifyUser({ id: 'u1', is_superuser: true, loginProvider: 'google' });
    expect(posthogMock.identify).toHaveBeenCalledWith('u1', { role: 'admin', login_provider: 'google' });
    const identifyProps = posthogMock.identify.mock.calls[0][1];
    expect(Object.keys(identifyProps)).not.toContain('email');
    registerAnalyticsContext({ is_guest: false, locale: 'zh-CN' });
    expect(posthogMock.register).toHaveBeenLastCalledWith({ is_guest: false, locale: 'zh-CN' });
    resetAnalyticsIdentity();
    expect(posthogMock.reset).toHaveBeenCalledTimes(1);
  });

  it('后台地址由采集地址推出，自托管地址保持不变', () => {
    expect(uiHostFromIngestHost('https://us.i.posthog.com')).toBe('https://us.posthog.com');
    expect(uiHostFromIngestHost('https://eu.i.posthog.com')).toBe('https://eu.posthog.com');
    expect(uiHostFromIngestHost('https://posthog.example.com')).toBe('https://posthog.example.com');
  });
});
