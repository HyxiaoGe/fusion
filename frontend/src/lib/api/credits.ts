import { API_CONFIG } from '@/lib/config';
import { apiRequest } from './fetchWithAuth';

export type CreditsTier = 'guest' | 'user' | 'admin' | 'exempt';

export interface CreditsWindow {
  /** 不限额的账号为 null */
  limit: number | null;
  used: number;
  remaining: number | null;
  resets_at: string;
}

/** 当前日、周窗口的额度；日窗口按北京时间自然日，周窗口从周一零点算。 */
export interface CreditsSummary {
  tier: CreditsTier;
  unlimited: boolean;
  daily: CreditsWindow;
  weekly: CreditsWindow;
  last_reset_at: string | null;
  reset_cards: ResetCards;
}

/** 管理员发放的重置卡：用一张清零当前日、周窗口的已用量，发放后 30 天内有效。 */
export interface ResetCards {
  available: number;
  /** 最早到期的那张卡的到期时间；没有可用卡为 null */
  next_expires_at: string | null;
}

export interface CreditsRound {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  credits: number;
  off_peak: boolean;
  unpriced: boolean;
}

export interface CreditsTool {
  tool: string;
  label: string;
  count: number;
  unit_credits: number;
  credits: number;
}

export interface CreditsBreakdown {
  model_credits: number;
  tool_credits: number;
  rounds: CreditsRound[];
  tools: CreditsTool[];
}

export interface CreditsEntry {
  id: string;
  kind: 'run' | 'reset';
  run_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  model_id: string | null;
  run_status: string | null;
  /** 非用户原因结束的运行不扣，credits 为 0，breakdown 仍记录实际用量 */
  charged: boolean;
  credits: number;
  breakdown: CreditsBreakdown | null;
  occurred_at: string;
  /** 重置记录的来源：用户用重置卡，或管理员重置；消耗记录为 null */
  source: 'reset_card' | 'admin' | null;
}

export interface MessageCredits {
  message_id: string;
  credits: number;
  /** 还在生成、尚未结算的运行数 */
  pending_runs: number;
  entries: CreditsEntry[];
}

export interface ConversationCredits {
  conversation_id: string;
  credits: number;
  /** 已结算的运行数 */
  runs: number;
  /** 还在生成、尚未结算的运行数 */
  pending_runs: number;
}

export interface AdminUserCredits {
  summary: CreditsSummary;
  items: CreditsEntry[];
  next_before: string | null;
}

const BASE = `${API_CONFIG.BASE_URL}/api/credits`;
const ADMIN_BASE = `${API_CONFIG.BASE_URL}/api/admin/credits`;

export function fetchMessageCredits(messageId: string, signal?: AbortSignal): Promise<MessageCredits> {
  return apiRequest<MessageCredits>(`${BASE}/messages/${encodeURIComponent(messageId)}`, signal ? { signal } : {});
}

export function fetchConversationCredits(conversationId: string, signal?: AbortSignal): Promise<ConversationCredits> {
  return apiRequest<ConversationCredits>(
    `${BASE}/conversations/${encodeURIComponent(conversationId)}`,
    signal ? { signal } : {},
  );
}

/** 用一张最早到期的重置卡，返回重置后的额度。 */
export function spendResetCard(): Promise<CreditsSummary> {
  return apiRequest<CreditsSummary>(`${BASE}/me/reset-card`, { method: 'POST' });
}

export function fetchAdminUserCredits(userId: string, signal?: AbortSignal): Promise<AdminUserCredits> {
  return apiRequest<AdminUserCredits>(
    `${ADMIN_BASE}/users/${encodeURIComponent(userId)}?limit=10`,
    signal ? { signal } : {},
  );
}

export function resetAdminUserCredits(userId: string): Promise<CreditsSummary> {
  return apiRequest<CreditsSummary>(`${ADMIN_BASE}/users/${encodeURIComponent(userId)}/reset`, { method: 'POST' });
}

export function grantAdminResetCards(userId: string, count: number): Promise<CreditsSummary> {
  return apiRequest<CreditsSummary>(`${ADMIN_BASE}/users/${encodeURIComponent(userId)}/reset-cards`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count }),
  });
}

/** 给每个受限登录账号各发一张重置卡（游客、管理员、免限额账号不发），返回发放张数。 */
export function grantResetCardsToAll(): Promise<{ granted: number }> {
  return apiRequest<{ granted: number }>(`${ADMIN_BASE}/reset-cards/grant-all`, { method: 'POST' });
}

export interface CreditsResetAll {
  /** 最近一次全员重置的时间；从未全员重置过为 null */
  last_reset_at: string | null;
}

export function fetchCreditsResetAll(signal?: AbortSignal): Promise<CreditsResetAll> {
  return apiRequest<CreditsResetAll>(`${ADMIN_BASE}/reset-all`, signal ? { signal } : {});
}

export function resetAllCredits(): Promise<CreditsResetAll> {
  return apiRequest<CreditsResetAll>(`${ADMIN_BASE}/reset-all`, { method: 'POST' });
}

/** 受限账号当前已用完的窗口；不限额或尚有余量返回 null。两个都用完时报周窗口，它恢复得更晚。 */
export function exhaustedCreditsWindow(summary: CreditsSummary | null | undefined): 'daily' | 'weekly' | null {
  if (!summary || summary.unlimited) return null;
  if (summary.weekly.remaining !== null && summary.weekly.remaining <= 0) return 'weekly';
  if (summary.daily.remaining !== null && summary.daily.remaining <= 0) return 'daily';
  return null;
}

export function formatCredits(value: number): string {
  if (value >= 100) return Math.round(value).toLocaleString('zh-CN');
  if (value >= 1) return value.toFixed(1).replace(/\.0$/, '');
  if (value > 0) return value.toFixed(2).replace(/0$/, '');
  return '0';
}

/** 额度用完时给用户看的提示；游客引导登录，有重置卡的提示去用卡，其余告知何时恢复。 */
export function creditsExhaustedMessage(window: 'daily' | 'weekly', isGuest: boolean, hasResetCard = false): string {
  if (hasResetCard) return window === 'daily' ? '今日额度已用完，可在额度里使用重置卡' : '本周额度已用完，可在额度里使用重置卡';
  if (isGuest) return window === 'daily' ? '今天免登录额度已用完，登录后继续' : '本周免登录额度已用完，登录后继续';
  return window === 'daily' ? '今日额度已用完，明天 0 点恢复' : '本周额度已用完，下周一 0 点恢复';
}
