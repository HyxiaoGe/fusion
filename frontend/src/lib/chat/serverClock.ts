/**
 * 服务端时间换算成本机时间。
 *
 * 每个样本 = 本机收到时刻 − 服务端发出时刻 = 两边时钟差 + 传输延迟，取最小值最接近真实时钟差。
 * 重连时服务端会先把已发生的事件一次性回放，这些事件的样本里带着事件本身的年龄，
 * 偏大的样本不会拉偏最小值；流开头的 server_clock 帧给出当下的服务端时间，保证回放前就有准确样本。
 * 本机休眠或校时后时钟差会变，样本只保留一段时间。
 */

const SAMPLE_TTL_MS = 10 * 60 * 1000;

let best: { offsetMs: number; observedAt: number } | null = null;

export function noteServerTime(serverMs: number, receivedAt: number = Date.now()): void {
  if (!Number.isFinite(serverMs) || serverMs <= 0) return;
  const offsetMs = receivedAt - serverMs;
  if (!best || offsetMs <= best.offsetMs || receivedAt - best.observedAt > SAMPLE_TTL_MS) {
    best = { offsetMs, observedAt: receivedAt };
  }
}

/** 服务端时间（毫秒）对应的本机时间；还没有样本时原样返回。 */
export function toLocalTime(serverMs: number): number {
  return serverMs + (best?.offsetMs ?? 0);
}

/** 服务端事件的 ts（秒）换成本机毫秒；缺失时返回 undefined，由调用方退回收到时刻。 */
export function localTimeOfEvent(tsSeconds: unknown): number | undefined {
  if (typeof tsSeconds !== 'number' || !Number.isFinite(tsSeconds) || tsSeconds <= 0) return undefined;
  return toLocalTime(tsSeconds * 1000);
}

export function resetServerClockForTest(): void {
  best = null;
}
