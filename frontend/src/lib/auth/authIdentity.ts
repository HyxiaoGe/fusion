import { jwtDecode } from 'jwt-decode';

interface AuthIdentityStateLike {
  auth?: {
    isAuthenticated?: boolean;
    user?: { id?: string | null } | null;
    token?: string | null;
  };
  guest?: {
    status?: string;
    userId?: string | null;
  };
}

interface AuthIdentityToken {
  sub?: unknown;
}

function normalizeIdentity(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function readTokenSubject(token: string | null | undefined): string | null {
  if (!token) return null;

  try {
    return normalizeIdentity(jwtDecode<AuthIdentityToken>(token).sub);
  } catch {
    return null;
  }
}

/**
 * 返回一次认证会话内稳定的身份键。
 *
 * Fusion API 用户资料的 id 与 auth-service JWT 的 sub 属于不同命名空间，
 * 因此不能用 user.id 判断是否切换账号；优先使用 JWT sub，只有旧状态没有
 * 可解码 token 时才回退到资料 id。
 * 未登录但已领取游客身份时返回 `guest:<游客用户 id>`，游客的会话、草稿与缓存
 * 都按这个键隔离；登录后键随之切换，退出登录后回到同一个游客键。
 */
export function selectStableAuthIdentity(
  state: AuthIdentityStateLike | null | undefined,
): string | null {
  const auth = state?.auth;
  if (!auth?.isAuthenticated) {
    const guest = state?.guest;
    const guestId = guest?.status === 'ready' ? normalizeIdentity(guest.userId) : null;
    return guestId ? `guest:${guestId}` : null;
  }

  return readTokenSubject(auth.token) ?? normalizeIdentity(auth.user?.id);
}
