type NotificationsChangedListener = (sessionKey?: string) => void;

const listeners = new Set<NotificationsChangedListener>();

// 正文展示后的已读回执与通知中心共享刷新入口；账号身份防止旧回执刷新新账号。
export function notifyNotificationsChanged(sessionKey?: string): void {
  listeners.forEach((listener) => listener(sessionKey));
}

export function subscribeNotificationsChanged(listener: NotificationsChangedListener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
