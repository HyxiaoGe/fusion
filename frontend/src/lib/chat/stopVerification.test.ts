import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getTrajectorySnapshot } from '@/lib/api/trajectory';
import type { TrajectorySnapshot } from '@/types/trajectory';
import { verifyStoppedRun, withStopDeadline } from './stopVerification';

vi.mock('@/lib/api/trajectory', () => ({ getTrajectorySnapshot: vi.fn() }));
const getSnapshot = vi.mocked(getTrajectorySnapshot);
const snapshot = (status: string, runId = 'run-1', messageId = 'message-1') => ({
  run: { run_id: runId, message_id: messageId, status },
}) as TrajectorySnapshot;

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
});
afterEach(() => vi.useRealTimers());

describe('withStopDeadline', () => {
  it('底层请求不响应取消时仍按时拒绝，并忽略迟到成功', async () => {
    let finish!: (value: string) => void;
    let signal!: AbortSignal;
    const result = withStopDeadline(currentSignal => {
      signal = currentSignal;
      return new Promise<string>(resolve => { finish = resolve; });
    }, 500);
    const checked = expect(result).rejects.toMatchObject({ name: 'TimeoutError', message: '停止请求超时' });
    await vi.advanceTimersByTimeAsync(500);
    await checked;
    expect(signal.aborted).toBe(true);
    finish('迟到成功');
    await Promise.resolve();
    await expect(result).rejects.toMatchObject({ name: 'TimeoutError' });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('父请求已经取消时不发送', async () => {
    const parent = new AbortController();
    parent.abort();
    const operation = vi.fn();
    await expect(withStopDeadline(operation, 500, parent.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(operation).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('父请求取消会结束无限等待并移除监听器', async () => {
    const parent = new AbortController();
    const remove = vi.spyOn(parent.signal, 'removeEventListener');
    const result = withStopDeadline(() => new Promise<string>(() => {}), 500, parent.signal);
    const checked = expect(result).rejects.toMatchObject({ name: 'AbortError' });
    parent.abort();
    await checked;
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it('正常完成后释放超时和父取消监听器', async () => {
    const parent = new AbortController();
    const remove = vi.spyOn(parent.signal, 'removeEventListener');
    await expect(withStopDeadline(async () => '已确认', 500, parent.signal)).resolves.toBe('已确认');
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('verifyStoppedRun', () => {
  it.each(['interrupted', 'completed', 'failed', 'incomplete', 'limit_reached'])('保留真实终态 %s', async status => {
    getSnapshot.mockResolvedValue(snapshot(status));
    await expect(verifyStoppedRun('conversation-1', 'run-1', 'message-1')).resolves.toBe(status);
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    expect(getSnapshot).toHaveBeenCalledWith('conversation-1', 'run-1', expect.any(AbortSignal));
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ['run-2', 'message-1'],
    ['run-1', 'message-2'],
  ])('拒绝其他运行或消息的终态 %s / %s', async (runId, messageId) => {
    getSnapshot.mockResolvedValue(snapshot('interrupted', runId, messageId));
    await expect(verifyStoppedRun('conversation-1', 'run-1', 'message-1')).resolves.toBeNull();
    expect(getSnapshot).toHaveBeenCalledTimes(1);
  });

  it('首次仍运行时隔 300ms 再查一次，不将 running 变成已取消', async () => {
    getSnapshot.mockResolvedValue(snapshot('running'));
    const result = verifyStoppedRun('conversation-1', 'run-1', 'message-1');
    await vi.advanceTimersByTimeAsync(299);
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toBeNull();
    expect(getSnapshot).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('核实请求永久挂起时 5 秒返回未知，迟到响应不会再次查询', async () => {
    let finish!: (value: TrajectorySnapshot) => void;
    getSnapshot.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const result = verifyStoppedRun('conversation-1', 'run-1', 'message-1');
    await vi.advanceTimersByTimeAsync(5000);
    await expect(result).resolves.toBeNull();
    finish(snapshot('running'));
    await vi.runAllTimersAsync();
    expect(getSnapshot).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('等待重查期间取消会清理间隔定时器，不再查询', async () => {
    const parent = new AbortController();
    getSnapshot.mockResolvedValue(snapshot('running'));
    const result = verifyStoppedRun('conversation-1', 'run-1', 'message-1', () => true, parent.signal);
    await vi.advanceTimersByTimeAsync(100);
    parent.abort();
    await expect(result).resolves.toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    expect(getSnapshot).toHaveBeenCalledTimes(1);
  });

  it('查询期间所属请求过期时不采用迟到终态', async () => {
    let current = true;
    let finish!: (value: TrajectorySnapshot) => void;
    getSnapshot.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const result = verifyStoppedRun('conversation-1', 'run-1', 'message-1', () => current);
    current = false;
    finish(snapshot('interrupted'));
    await expect(result).resolves.toBeNull();
    expect(getSnapshot).toHaveBeenCalledTimes(1);
  });

  it('已过期或已取消时不查询', async () => {
    const parent = new AbortController();
    parent.abort();
    await expect(verifyStoppedRun('conversation-1', 'run-1', 'message-1', () => true, parent.signal)).resolves.toBeNull();
    await expect(verifyStoppedRun('conversation-1', 'run-1', 'message-1', () => false)).resolves.toBeNull();
    expect(getSnapshot).not.toHaveBeenCalled();
  });
});
