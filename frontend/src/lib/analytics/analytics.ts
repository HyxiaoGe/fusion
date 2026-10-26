/**
 * 产品埋点客户端：对 PostHog 的唯一封装。业务代码只调这里的 track / identify，不直接碰 posthog-js。
 *
 * - 没配 NEXT_PUBLIC_POSTHOG_KEY（本地开发、测试、CI 构建）时所有调用都是空操作。
 * - 浏览器经同源 /ingest 反代到 PostHog（见 next.config.js），避开广告拦截与跨境连通性问题，
 *   也让管理页更严的 CSP 不用放行第三方域名。
 * - 身份：未登录按 PostHog 匿名 ID；登录后 identify 成 Fusion user_id，之前的匿名行为自动并入。
 *   人的属性只有角色与登录方式，不送邮箱、昵称。
 * - 回放：输入一律遮罩；含对话内容的区域加 `ph-no-capture` 类名整块不录（PostHog 默认识别）：
 *   消息列表、输入框、会话侧栏（标题由对话生成）、文档面板、知识库切片预览。
 */

import posthog from 'posthog-js';
import type { AnalyticsEventName, AnalyticsEvents } from './events';

/** 同源反代路径，和 next.config.js 的 rewrites 一致。 */
export const ANALYTICS_INGEST_PATH = '/ingest';

let initialized = false;

function projectKey(): string {
  return (process.env.NEXT_PUBLIC_POSTHOG_KEY ?? '').trim();
}

/** PostHog 后台地址由采集地址推出：us.i.posthog.com → us.posthog.com，自托管时两者相同。 */
export function uiHostFromIngestHost(ingestHost: string): string {
  return ingestHost.replace(/^(https?:\/\/[a-z0-9-]+)\.i\.posthog\.com/i, '$1.posthog.com');
}

export function isAnalyticsEnabled(): boolean {
  return initialized;
}

/** 在浏览器里初始化一次；没配 key 或在服务端时什么都不做。返回是否已启用。 */
export function initAnalytics(): boolean {
  if (initialized) return true;
  if (typeof window === 'undefined') return false;
  const key = projectKey();
  if (!key) return false;
  posthog.init(key, {
    api_host: `${window.location.origin}${ANALYTICS_INGEST_PATH}`,
    ui_host: uiHostFromIngestHost((process.env.NEXT_PUBLIC_POSTHOG_HOST ?? '').trim() || 'https://us.i.posthog.com'),
    // App Router 没有整页跳转，按 history 变化记页面浏览。
    capture_pageview: 'history_change',
    capture_pageleave: true,
    // 匿名访客不建人档，登录 identify 后才建；省额度也省掉一堆没意义的游客档案。
    person_profiles: 'identified_only',
    capture_exceptions: true,
    autocapture: true,
    session_recording: { maskAllInputs: true },
    disable_surveys: true,
  });
  posthog.register({ app_version: process.env.NEXT_PUBLIC_BUILD_VERSION ?? '' });
  initialized = true;
  return true;
}

type EventArgs<E extends AnalyticsEventName> = AnalyticsEvents[E] extends Record<string, never>
  ? [properties?: AnalyticsEvents[E]]
  : [properties: AnalyticsEvents[E]];

/** 记一个事件表里定义过的事件；未启用时空操作。 */
export function track<E extends AnalyticsEventName>(event: E, ...args: EventArgs<E>): void {
  if (!initialized) return;
  posthog.capture(event, args[0] ?? {});
}

export interface AnalyticsIdentity {
  id: string;
  is_superuser: boolean;
  loginProvider?: string | null;
}

/** 登录后把匿名访客并到 Fusion 用户；人的属性只带角色与登录方式。 */
export function identifyUser(user: AnalyticsIdentity): void {
  if (!initialized) return;
  posthog.identify(user.id, {
    role: user.is_superuser ? 'admin' : 'user',
    login_provider: user.loginProvider ?? null,
  });
}

/** 登出后换一个新的匿名身份，后续行为不再算到刚退出的用户头上。 */
export function resetAnalyticsIdentity(): void {
  if (!initialized) return;
  posthog.reset();
}

export interface AnalyticsContext {
  is_guest: boolean;
  locale: string;
}

/** 随每个事件一起带上的公共属性；状态变化时重新注册即可。 */
export function registerAnalyticsContext(context: AnalyticsContext): void {
  if (!initialized) return;
  posthog.register({ ...context });
}

/** 失败事件里的 error_code：优先取业务错误码，其次错误类名；绝不带错误文案。 */
export function errorCodeOf(error: unknown): string {
  if (error && typeof error === 'object') {
    const code = (error as { code?: unknown }).code;
    if (typeof code === 'string' && code) return code;
    if (error instanceof Error && error.name) return error.name;
  }
  return 'unknown';
}

/** 仅测试用：让每个用例从未初始化状态开始。 */
export function resetAnalyticsForTests(): void {
  initialized = false;
}
