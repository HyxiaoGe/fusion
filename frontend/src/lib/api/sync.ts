import { API_CONFIG } from '@/lib/config';
import type { ConversationActivity } from './chat';
import { apiRequest } from './fetchWithAuth';

type SyncSection = 'viewing' | 'activity' | 'notifications';

interface SyncResponse {
  viewing: boolean | null;
  activity: ConversationActivity | null;
  notifications: { revision: number } | null;
  failed: SyncSection[];
}

interface Waiter {
  section: SyncSection;
  conversationId?: string;
  resolve: (data: SyncResponse) => void;
  reject: (error: unknown) => void;
  signal?: AbortSignal;
}

const syncPath = `${API_CONFIG.BASE_URL}/api/sync`;
let pending: Waiter[] | null = null;

function abortError(): DOMException {
  return new DOMException('Aborted', 'AbortError');
}

async function flush() {
  const waiters = (pending ?? []).filter(waiter => !waiter.signal?.aborted);
  pending = null;
  if (!waiters.length) return;
  // 同一批里只会有当前对话页的心跳；切换对话时以最后登记的为准。
  const viewing = waiters.filter(waiter => waiter.section === 'viewing').at(-1)?.conversationId;
  const body = {
    ...(viewing ? { viewing_conversation_id: viewing } : {}),
    activity: waiters.some(waiter => waiter.section === 'activity'),
    notifications: waiters.some(waiter => waiter.section === 'notifications'),
  };
  try {
    const data = await apiRequest<SyncResponse>(syncPath, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    for (const waiter of waiters) waiter.resolve(data);
  } catch (error) {
    for (const waiter of waiters) waiter.reject(error);
  }
}

/** 同一轮事件循环里登记的部分合成一个请求发出；各调用方只拿自己那部分，失败也只影响自己。 */
function enqueue(section: SyncSection, signal?: AbortSignal, conversationId?: string): Promise<SyncResponse> {
  if (signal?.aborted) return Promise.reject(abortError());
  if (!pending) {
    pending = [];
    // 用宏任务而非微任务收批：调度器同一节拍里的任务即使先经过 await 再登记也能赶上。
    setTimeout(() => { void flush(); }, 0);
  }
  const batch = pending;
  return new Promise((resolve, reject) => {
    const waiter: Waiter = { section, conversationId, resolve, reject, signal };
    batch.push(waiter);
    signal?.addEventListener('abort', () => reject(abortError()), { once: true });
  });
}

function sectionFailed(section: SyncSection): Error {
  return new Error(`sync section failed: ${section}`);
}

/** 续期「正在看」标记。 */
export async function syncConversationViewing(conversationId: string, signal?: AbortSignal): Promise<void> {
  const data = await enqueue('viewing', signal, conversationId);
  if (data.viewing !== true) throw sectionFailed('viewing');
}

/** 当前用户正在生成、以及生成完还没打开过的对话。 */
export async function syncConversationActivity(signal?: AbortSignal): Promise<ConversationActivity> {
  const data = await enqueue('activity', signal);
  if (!data.activity) throw sectionFailed('activity');
  return { streaming: data.activity.streaming ?? [], unread: data.activity.unread ?? [] };
}

/** 通知版本号；读状态变化也会推进，版本不变时列表无需重取。 */
export async function syncNotificationsRevision(signal?: AbortSignal): Promise<number> {
  const data = await enqueue('notifications', signal);
  if (!data.notifications) throw sectionFailed('notifications');
  return data.notifications.revision;
}

export function resetSyncBatchForTests() {
  pending = null;
}
