import { describe, expect, it } from 'vitest';
import { selectStableAuthIdentity } from './authIdentity';

function createUnsignedToken(subject: string): string {
  const encode = (value: object) => btoa(JSON.stringify(value))
    .replaceAll('+', '-')
    .replaceAll('/', '_')
    .replaceAll('=', '');
  return `${encode({ alg: 'none', typ: 'JWT' })}.${encode({ sub: subject })}.`;
}

describe('selectStableAuthIdentity', () => {
  it('优先使用 JWT sub，避免 Fusion 用户资料 id 替换造成会话抖动', () => {
    expect(selectStableAuthIdentity({
      auth: {
        isAuthenticated: true,
        token: createUnsignedToken('auth-user-a'),
        user: { id: 'fusion-user-a' },
      },
    })).toBe('auth-user-a');
  });

  it('旧状态 token 不可解码时回退到用户资料 id', () => {
    expect(selectStableAuthIdentity({
      auth: {
        isAuthenticated: true,
        token: 'legacy-token',
        user: { id: 'fusion-user-a' },
      },
    })).toBe('fusion-user-a');
  });

  it('未登录但已领取游客身份时返回游客键，登录后以登录身份为准', () => {
    expect(selectStableAuthIdentity({
      auth: { isAuthenticated: false },
      guest: { status: 'ready', userId: 'guest-1' },
    })).toBe('guest:guest-1');
    expect(selectStableAuthIdentity({
      auth: { isAuthenticated: false },
      guest: { status: 'loading', userId: 'guest-1' },
    })).toBeNull();
    expect(selectStableAuthIdentity({
      auth: { isAuthenticated: true, token: createUnsignedToken('auth-user-a') },
      guest: { status: 'ready', userId: 'guest-1' },
    })).toBe('auth-user-a');
  });

  it('未登录时不返回残留身份', () => {
    expect(selectStableAuthIdentity({
      auth: {
        isAuthenticated: false,
        token: createUnsignedToken('auth-user-a'),
        user: { id: 'fusion-user-a' },
      },
    })).toBeNull();
  });
});
