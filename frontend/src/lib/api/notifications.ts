import { API_CONFIG } from '@/lib/config';
import { apiRequest } from './fetchWithAuth';

export type NotificationKind =
  | 'run_completed'
  | 'run_failed'
  | 'run_limit_reached'
  | 'run_incomplete'
  | 'run_interrupted'
  | 'changelog_published';
export type NotificationFilter = 'all' | 'unread';
export type NotificationBusinessType = 'ai_conversation' | 'changelog';

export type NotificationTarget = {
  type: 'conversation';
  conversation_id: string;
  message_id: string;
  run_id: string;
} | { type: 'changelog'; changelog_id: string };

export interface NotificationItem {
  id: string;
  business_type: NotificationBusinessType;
  kind: NotificationKind;
  title: string;
  body: string;
  created_at: string;
  read_at: string | null;
  created_revision: number;
  target: NotificationTarget;
}

interface NotificationSnapshot {
  unread_count: number;
  unread_conversation_ids: string[];
  revision: number;
}

export interface NotificationPage extends NotificationSnapshot {
  items: NotificationItem[];
  next_cursor: string | null;
}

export interface NotificationReadResponse extends NotificationSnapshot {
  updated_count: number;
}

interface NotificationResultsReadRequest {
  conversation_id: string;
  results: { run_id: string; message_id: string }[];
}

const notificationsPath = `${API_CONFIG.BASE_URL}/api/notifications`;

export function getNotifications(
  params: { filter?: NotificationFilter; cursor?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<NotificationPage> {
  const query = new URLSearchParams({ filter: params.filter ?? 'all', limit: String(params.limit ?? 20) });
  if (params.cursor) query.set('cursor', params.cursor);
  return apiRequest<NotificationPage>(`${notificationsPath}?${query}`, { signal });
}

function postRead(path: string, body: unknown, signal?: AbortSignal): Promise<NotificationReadResponse> {
  return apiRequest<NotificationReadResponse>(`${notificationsPath}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}

export function markNotificationsRead(ids: string[], signal?: AbortSignal): Promise<NotificationReadResponse> {
  return postRead('read', { ids }, signal);
}

export function markAllNotificationsRead(throughRevision: number, signal?: AbortSignal): Promise<NotificationReadResponse> {
  return postRead('read-all', { through_revision: throughRevision }, signal);
}

export function markNotificationResultsRead(
  body: NotificationResultsReadRequest,
  signal?: AbortSignal,
): Promise<NotificationReadResponse> {
  return postRead('read-result', body, signal);
}

function viewingPath(conversationId: string): string {
  return `${notificationsPath}/viewing/${encodeURIComponent(conversationId)}`;
}

/** 离开或切到后台时撤掉标记；keepalive 让页面关闭时请求仍能发出。 */
export function clearConversationViewing(conversationId: string): Promise<null> {
  return apiRequest<null>(viewingPath(conversationId), { method: 'DELETE', keepalive: true });
}
