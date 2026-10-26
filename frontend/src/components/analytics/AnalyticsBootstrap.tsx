'use client';

import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useAppSelector } from '@/redux/hooks';
import { selectIsGuest } from '@/redux/selectors';
import {
  identifyUser,
  initAnalytics,
  registerAnalyticsContext,
  resetAnalyticsIdentity,
} from '@/lib/analytics/analytics';

/**
 * 埋点的生命周期挂在这里：挂载时初始化，用户态变化时 identify / reset，公共属性随状态刷新。
 * 不渲染任何东西。登录成功本身的事件在各登录入口记（区分 SSO / 邮箱），这里只管身份归并。
 */
export default function AnalyticsBootstrap() {
  // 有的测试只 mock 了 t，没有 i18n；语言拿不到就留空，不影响其他埋点。
  const { i18n } = useTranslation();
  const locale = i18n?.language ?? '';
  const user = useAppSelector((state) => state.auth.user);
  const isAuthenticated = useAppSelector((state) => state.auth.isAuthenticated);
  const isGuest = useAppSelector(selectIsGuest);
  const userId = isAuthenticated ? user?.id ?? null : null;
  const isSuperuser = user?.is_superuser ?? false;
  const loginProvider = user?.loginProvider ?? null;
  const identifiedUserId = useRef<string | null>(null);

  useEffect(() => {
    initAnalytics();
  }, []);

  useEffect(() => {
    if (userId) {
      if (identifiedUserId.current !== userId) {
        identifyUser({ id: userId, is_superuser: isSuperuser, loginProvider });
        identifiedUserId.current = userId;
      }
      return;
    }
    // 从登录态变成未登录（退出、会话失效）才 reset；首屏尚未恢复会话时不动匿名身份。
    if (identifiedUserId.current !== null) {
      resetAnalyticsIdentity();
      identifiedUserId.current = null;
    }
  }, [userId, isSuperuser, loginProvider]);

  useEffect(() => {
    registerAnalyticsContext({ is_guest: isGuest, locale });
  }, [isGuest, locale]);

  return null;
}
