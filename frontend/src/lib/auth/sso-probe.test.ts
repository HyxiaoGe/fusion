import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { canAutoResumeSession, clearExplicitLogout, markExplicitLogout } from './sso-probe';

const LOGGED_OUT_KEY = 'fusion_sso_logged_out';
const ACCESS_TOKEN_KEY = 'auth_token';

describe('sso-probe: 未登录标签页的中央会话恢复边界', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('没有本地 token 时允许无跳转恢复', () => {
    expect(canAutoResumeSession('/chat')).toBe(true);
  });

  it('已有本地 token 时不恢复', () => {
    localStorage.setItem(ACCESS_TOKEN_KEY, 'tok');
    expect(canAutoResumeSession('/chat')).toBe(false);
  });

  it('回调换码期间不恢复', () => {
    expect(canAutoResumeSession('/auth/callback?code=abc&state=xyz')).toBe(false);
  });

  it('显式登出后不恢复，刷新也不能绕过', () => {
    markExplicitLogout();
    expect(sessionStorage.getItem(LOGGED_OUT_KEY)).toBe('1');
    expect(canAutoResumeSession('/chat')).toBe(false);
  });

  it('重新主动登录时清掉登出守卫', () => {
    markExplicitLogout();
    clearExplicitLogout();
    expect(sessionStorage.getItem(LOGGED_OUT_KEY)).toBeNull();
    expect(canAutoResumeSession('/chat')).toBe(true);
  });

  it('sessionStorage 读取失败时保守放弃', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(canAutoResumeSession('/chat')).toBe(false);
  });
});
