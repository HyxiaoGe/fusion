/**
 * 游客会话：未登录用户向 fusion-api 领取的匿名身份。
 *
 * 游客令牌单独存在 `guest_session`，不进 SSO SDK 的 `auth_token`：SDK 会把不认识的
 * 令牌当成过期票去刷新。请求时只有没有登录令牌才带游客令牌（见 fetchWithAuth），
 * 所以登录后游客会话原样保留、不再使用；退出登录后又回到原来的游客身份和游客会话。
 */

import { API_CONFIG } from '@/lib/config';

const GUEST_SESSION_KEY = 'guest_session';
// 提前一分钟视为过期，避免请求途中刚好过期
const EXPIRY_MARGIN_SECONDS = 60;

export interface GuestSession {
  token: string;
  userId: string;
  expiresAt: number;
}

export type GuestSessionResult =
  | { status: 'ready'; session: GuestSession }
  | { status: 'unavailable'; code: string; message: string };

function isGuestSession(value: unknown): value is GuestSession {
  if (typeof value !== 'object' || value === null) return false;
  const session = value as Record<string, unknown>;
  return typeof session.token === 'string' && session.token.length > 0
    && typeof session.userId === 'string' && session.userId.length > 0
    && typeof session.expiresAt === 'number';
}

// 存储不可用（无痕模式等）时本页仍用内存里的会话
let memorySession: GuestSession | null = null;

function isUnexpired(session: GuestSession): boolean {
  return session.expiresAt - EXPIRY_MARGIN_SECONDS > Date.now() / 1000;
}

function readStoredSession(): GuestSession | null {
  try {
    const raw = localStorage.getItem(GUEST_SESSION_KEY);
    if (!raw) return null;
    const session: unknown = JSON.parse(raw);
    return isGuestSession(session) ? session : null;
  } catch {
    return null;
  }
}

export function readGuestSession(): GuestSession | null {
  if (typeof window === 'undefined') return null;
  const session = readStoredSession() ?? memorySession;
  return session && isUnexpired(session) ? session : null;
}

export function getStoredGuestToken(): string | null {
  return readGuestSession()?.token ?? null;
}

export function clearGuestSession(): void {
  memorySession = null;
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(GUEST_SESSION_KEY);
  } catch {
    // 存储不可用时没有可清的会话
  }
}

let pendingRequest: Promise<GuestSessionResult> | null = null;

async function requestGuestSession(): Promise<GuestSessionResult> {
  let response: Response;
  try {
    response = await fetch(`${API_CONFIG.BASE_URL}/api/auth/guest`, { method: 'POST' });
  } catch {
    return { status: 'unavailable', code: 'NETWORK_ERROR', message: '网络连接失败' };
  }
  const body = await response.json().catch(() => null) as {
    code?: string;
    message?: string;
    data?: { available?: boolean; access_token?: string; expires_at?: number; user?: { id?: string } };
  } | null;
  const data = body?.data;
  if (response.ok && data?.available === false) {
    return { status: 'unavailable', code: 'GUEST_MODE_DISABLED', message: '暂未开放免登录使用' };
  }
  if (!response.ok || body?.code !== 'SUCCESS' || !data?.access_token || !data.user?.id) {
    return {
      status: 'unavailable',
      code: body?.code || `HTTP_${response.status}`,
      message: body?.message || '暂时无法免登录使用',
    };
  }
  const session: GuestSession = {
    token: data.access_token,
    userId: data.user.id,
    expiresAt: typeof data.expires_at === 'number' ? data.expires_at : 0,
  };
  memorySession = session;
  try {
    localStorage.setItem(GUEST_SESSION_KEY, JSON.stringify(session));
  } catch {
    // 存储不可用：本页用内存会话，刷新后会重新领取
  }
  return { status: 'ready', session };
}

/** 已有未过期的游客会话直接复用，否则领取一个；并发调用共用同一次请求。 */
export function ensureGuestSession(): Promise<GuestSessionResult> {
  const existing = readGuestSession();
  if (existing) return Promise.resolve({ status: 'ready', session: existing });
  if (!pendingRequest) {
    pendingRequest = requestGuestSession().finally(() => {
      pendingRequest = null;
    });
  }
  return pendingRequest;
}
