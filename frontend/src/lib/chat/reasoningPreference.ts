const STORAGE_KEY = 'fusion.deepThinkingEnabled';

/** 深度思考默认关闭；用户手动开启后记在本浏览器，刷新后保持。 */
export function readDeepThinkingPreference(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeDeepThinkingPreference(enabled: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    if (enabled) localStorage.setItem(STORAGE_KEY, '1');
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // localStorage 不可用时只在本次会话内生效
  }
}
