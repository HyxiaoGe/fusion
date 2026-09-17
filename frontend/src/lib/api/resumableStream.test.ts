import { describe, expect, it, vi } from 'vitest';
import { StreamRequestError, type StreamCallbacks } from './chat';
import {
  DEFAULT_STREAM_RECONNECT_DELAYS_MS,
  runResumableStream,
} from './resumableStream';

function callbacks(): StreamCallbacks {
  return {
    onEntryId: vi.fn(),
    onReady: vi.fn(),
    onReasoning: vi.fn(),
    onAnswering: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  };
}

function recoverableError(message: string) {
  return new StreamRequestError(message, { recoverable: true });
}

describe('runResumableStream', () => {
  it('默认使用 0/250/750ms 三次有限重连', () => {
    expect(DEFAULT_STREAM_RECONNECT_DELAYS_MS).toEqual([0, 250, 750]);
  });

  it('初始流只打开一次，并用包装后的 onEntryId 独占递进安全 cursor', async () => {
    const consumerCallbacks = callbacks();
    const openInitial = vi.fn(async (wrapped: StreamCallbacks) => {
      wrapped.onEntryId?.('10-1');
      throw recoverableError('初始流断开');
    });
    const openReconnect = vi
      .fn()
      .mockImplementationOnce(async (cursor: string, wrapped: StreamCallbacks) => {
        expect(cursor).toBe('10-1');
        wrapped.onEntryId?.('10-2');
        throw recoverableError('续传再次断开');
      })
      .mockImplementationOnce(async (cursor: string, wrapped: StreamCallbacks) => {
        expect(cursor).toBe('10-2');
        wrapped.onDone({ messageId: 'm1', conversationId: 'c1' });
      });
    const onPhaseChange = vi.fn();

    await runResumableStream({
      callbacks: consumerCallbacks,
      signal: new AbortController().signal,
      openInitial,
      openReconnect,
      retryDelaysMs: [0, 0, 0],
      onPhaseChange,
    });

    expect(openInitial).toHaveBeenCalledTimes(1);
    expect(openReconnect).toHaveBeenCalledTimes(2);
    expect(consumerCallbacks.onEntryId).toHaveBeenNthCalledWith(1, '10-1');
    expect(consumerCallbacks.onEntryId).toHaveBeenNthCalledWith(2, '10-2');
    expect(onPhaseChange).toHaveBeenCalledWith('reconnecting');
  });

  it('不可恢复错误不重连，可恢复错误最多重连三次', async () => {
    const openReconnect = vi.fn().mockRejectedValue(recoverableError('仍失败'));
    await expect(runResumableStream({
      callbacks: callbacks(),
      signal: new AbortController().signal,
      openInitial: vi.fn().mockRejectedValue(recoverableError('断线')),
      openReconnect,
      retryDelaysMs: [0, 0, 0],
    })).rejects.toThrow('仍失败');
    expect(openReconnect).toHaveBeenCalledTimes(3);

    const terminalReconnect = vi.fn();
    await expect(runResumableStream({
      callbacks: callbacks(),
      signal: new AbortController().signal,
      openInitial: vi.fn().mockRejectedValue(new StreamRequestError('终态', { recoverable: false })),
      openReconnect: terminalReconnect,
      retryDelaysMs: [0, 0, 0],
    })).rejects.toThrow('终态');
    expect(terminalReconnect).not.toHaveBeenCalled();
  });

  it('终态补发判定跨重连有效：done 派发过之后不再允许补发', async () => {
    // issue #74：解析层在"流终止却无 done 信封"时补发终态，但它的标志只在单次
    // 调用内有效。重连是新一次调用——若 done 其实早已送达，空回放会被误判为
    // 从未收到终态而重复完成。该判定必须由跨重连的这一层给出。
    const consumerCallbacks = callbacks();
    const seen: boolean[] = [];

    const openInitial = vi.fn(async (wrapped: StreamCallbacks) => {
      seen.push(wrapped.shouldSynthesizeTerminal?.() ?? true);
      wrapped.onDone({ messageId: 'm1', conversationId: 'c1' });
      throw recoverableError('断线');
    });
    const openReconnect = vi.fn(async (_id: string, wrapped: StreamCallbacks) => {
      seen.push(wrapped.shouldSynthesizeTerminal?.() ?? true);
    });

    await runResumableStream({
      callbacks: consumerCallbacks,
      signal: new AbortController().signal,
      openInitial,
      openReconnect,
      retryDelaysMs: [0],
    });

    // 首次尚未派发终态时允许补发；done 送达后重连不得再补
    expect(seen).toEqual([true, false]);
    expect(consumerCallbacks.onDone).toHaveBeenCalledTimes(1);
  });

  it('abort 能中断退避等待且不再打开 reconnect', async () => {
    const controller = new AbortController();
    const openReconnect = vi.fn();
    const run = runResumableStream({
      callbacks: callbacks(),
      signal: controller.signal,
      openInitial: vi.fn().mockRejectedValue(recoverableError('断线')),
      openReconnect,
      retryDelaysMs: [1000],
    });

    controller.abort();

    await expect(run).rejects.toMatchObject({ name: 'AbortError' });
    expect(openReconnect).not.toHaveBeenCalled();
  });
});
