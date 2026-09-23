import type { AgentRunStatus } from '@/types/agentRun';

const STORAGE_PREFIX = 'fusion:stop-outcome:';
const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const TERMINAL_STATUSES = new Set<AgentRunStatus>([
  'interrupted', 'completed', 'failed', 'incomplete', 'limit_reached',
]);

export interface StopOutcomeNotice {
  authIdentity: string;
  conversationId: string;
  runId: string;
  messageId: string | null;
  requestedAt: number;
  terminalStatus: Exclude<AgentRunStatus, 'running'> | null;
}

function storageKey(conversationId: string): string {
  return `${STORAGE_PREFIX}${conversationId}`;
}

function isValidNotice(value: unknown): value is StopOutcomeNotice {
  if (!value || typeof value !== 'object') return false;
  const notice = value as Partial<StopOutcomeNotice>;
  return typeof notice.authIdentity === 'string' && Boolean(notice.authIdentity)
    && typeof notice.conversationId === 'string' && Boolean(notice.conversationId)
    && typeof notice.runId === 'string' && Boolean(notice.runId)
    && (notice.messageId === null || typeof notice.messageId === 'string')
    && typeof notice.requestedAt === 'number' && Number.isFinite(notice.requestedAt)
    && (notice.terminalStatus === null || TERMINAL_STATUSES.has(notice.terminalStatus as AgentRunStatus));
}

/** 仅保存停止请求的身份与结果，不保存消息正文或服务端运行状态。 */
export function saveStopOutcomeNotice(notice: StopOutcomeNotice): void {
  if (typeof window === 'undefined' || !isValidNotice(notice)) return;
  try {
    window.sessionStorage.setItem(storageKey(notice.conversationId), JSON.stringify(notice));
  } catch {
    // 存储不可用时仍保留当前页面的停止反馈。
  }
}

export function readStopOutcomeNotice(conversationId: string, authIdentity: string | null): StopOutcomeNotice | null {
  if (typeof window === 'undefined' || !authIdentity) return null;
  try {
    const key = storageKey(conversationId);
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const notice: unknown = JSON.parse(raw);
    if (!isValidNotice(notice) || notice.conversationId !== conversationId
      || notice.authIdentity !== authIdentity
      || notice.requestedAt > Date.now() + 60_000
      || Date.now() - notice.requestedAt > MAX_AGE_MS) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return notice;
  } catch {
    return null;
  }
}

/** 迟到的旧 Run 不能清除新一轮的提示。 */
export function clearStopOutcomeNotice(conversationId: string, runId: string): void {
  if (typeof window === 'undefined') return;
  try {
    const key = storageKey(conversationId);
    const raw = window.sessionStorage.getItem(key);
    if (raw && (JSON.parse(raw) as Partial<StopOutcomeNotice>).runId === runId) {
      window.sessionStorage.removeItem(key);
    }
  } catch {
    // 清理失败不影响运行状态。
  }
}
