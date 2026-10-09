'use client';

import { NotificationsProvider } from '@/components/notifications/NotificationsProvider';
import { useAppSelector } from '@/redux/hooks';
import { selectAuthSessionKey } from '@/redux/selectors';

// 更新日志是独立阅读页，不挂聊天侧栏；仍需通知上下文确认已读并同步其他页面的未读数。
export default function UpdatesLayout({ children }: { children: React.ReactNode }) {
  const authSessionKey = useAppSelector(selectAuthSessionKey);
  return <NotificationsProvider sessionKey={authSessionKey}>{children}</NotificationsProvider>;
}
