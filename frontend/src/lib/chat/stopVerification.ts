import { getTrajectorySnapshot } from '@/lib/api/trajectory';
import type { AgentRunStatus } from '@/types/agentRun';

/** 即使底层请求忽略 AbortSignal，也按时结束等待；迟到响应不能再次完成操作。 */
export function withStopDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  timeoutMs: number,
  parentSignal?: AbortSignal,
): Promise<T> {
  if (parentSignal?.aborted) return Promise.reject(new DOMException('停止请求已取消', 'AbortError'));
  return new Promise<T>((resolve, reject) => {
    const controller = new AbortController();
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      parentSignal?.removeEventListener('abort', onAbort);
    };
    const cancel = (error: DOMException) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
      controller.abort();
    };
    const onAbort = () => cancel(new DOMException('停止请求已取消', 'AbortError'));
    const timer = setTimeout(() => cancel(new DOMException('停止请求超时', 'TimeoutError')), timeoutMs);
    parentSignal?.addEventListener('abort', onAbort, { once: true });
    try {
      Promise.resolve(operation(controller.signal)).then(
        value => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve(value);
        },
        error => {
          if (settled) return;
          settled = true;
          cleanup();
          reject(error);
        },
      );
    } catch (error) {
      settled = true;
      cleanup();
      reject(error);
    }
  });
}

function waitForVerificationRetry(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('停止核实已取消', 'AbortError'));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      reject(new DOMException('停止核实已取消', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, 300);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/** 只核实原运行的终态；未取得证据时返回 null，不推定取消成功。 */
export async function verifyStoppedRun(
  conversationId: string,
  runId: string,
  messageId: string | null,
  isCurrent: () => boolean = () => true,
  parentSignal?: AbortSignal,
): Promise<Exclude<AgentRunStatus, 'running'> | null> {
  try {
    return await withStopDeadline(async signal => {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        if (signal.aborted || !isCurrent()) return null;
        if (attempt) await waitForVerificationRetry(signal);
        if (signal.aborted || !isCurrent()) return null;
        const { run } = await getTrajectorySnapshot(conversationId, runId, signal);
        if (signal.aborted || !isCurrent()) return null;
        if (run.run_id !== runId || (messageId && run.message_id !== messageId)) return null;
        const terminal = (['interrupted', 'completed', 'failed', 'incomplete', 'limit_reached'] as const)
          .find(status => status === run.status);
        if (terminal) return terminal;
      }
      return null;
    }, 5000, parentSignal);
  } catch {
    // 查询失败或超时都不构成终态证据。
    return null;
  }
}
