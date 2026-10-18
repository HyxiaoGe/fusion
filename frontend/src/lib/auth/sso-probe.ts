/**
 * 未登录标签页的中央会话恢复边界。
 *
 * 恢复本身走 SDK resumeSession（POST /auth/session/resume，无页面跳转）；这里只保留宿主侧守卫：
 *  - 回调换码期间（/auth/callback）不恢复，避免冲掉正在进行的换码。
 *  - 显式登出后落守卫，刷新也不能绕过，防止被中央会话静默重新登入。
 *  - sessionStorage 不可用时放弃自动恢复（失败保守）。
 */

const LOGGED_OUT_KEY = 'fusion_sso_logged_out';
const ACCESS_TOKEN_KEY = 'auth_token';
const CALLBACK_PATH = '/auth/callback';

function session(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

/** 落「已登出/勿自动重登」守卫——登出时调用以阻止登出后被静默重新登入。 */
export function markExplicitLogout(): void {
  try {
    session()?.setItem(LOGGED_OUT_KEY, '1');
  } catch {
    // ignore
  }
}

/**
 * 用户重新主动登录代表开启了新的认证生命周期；清掉过去显式登出的守卫，避免同一标签页
 * 此后永久无法参与自动会话恢复。
 */
export function clearExplicitLogout(): void {
  try {
    session()?.removeItem(LOGGED_OUT_KEY);
  } catch {
    // ignore
  }
}

/**
 * 未登录标签页能否执行一次无跳转的中央会话恢复探测。
 *
 * 真正的 Cookie 探测、PKCE 换票和 token/user 原子提交由 SDK resumeSession 负责；这里仅保留
 * 回调页、显式登出和本地会话三道宿主边界。sessionStorage 不可用时失败保守。
 */
export function canAutoResumeSession(currentPath: string): boolean {
  if (typeof window === 'undefined') return false;
  if (currentPath.startsWith(CALLBACK_PATH)) return false;
  const s = session();
  if (!s) return false;
  try {
    if (s.getItem(LOGGED_OUT_KEY)) return false;
    return !window.localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return false;
  }
}
