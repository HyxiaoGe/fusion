import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SCHEDULER_TICK_MS,
  registerBackgroundTask,
  resetBackgroundSchedulerForTests,
} from './backgroundScheduler';

let visibility: DocumentVisibilityState = 'visible';
let originalVisibility: PropertyDescriptor | undefined;

function setVisibility(next: DocumentVisibilityState) {
  visibility = next;
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('backgroundScheduler', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    visibility = 'visible';
    originalVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  });

  afterEach(() => {
    resetBackgroundSchedulerForTests();
    vi.useRealTimers();
    if (originalVisibility) Object.defineProperty(document, 'visibilityState', originalVisibility);
    else Reflect.deleteProperty(document, 'visibilityState');
  });

  it('多个任务共用一个定时器，登记即跑并在同一节拍一起触发', () => {
    const setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    const a = vi.fn();
    const b = vi.fn();
    registerBackgroundTask(a, { intervalMs: SCHEDULER_TICK_MS });
    registerBackgroundTask(b, { intervalMs: SCHEDULER_TICK_MS });
    expect(setIntervalSpy).toHaveBeenCalledTimes(1);
    expect(a).toHaveBeenCalledWith('start');
    expect(b).toHaveBeenCalledWith('start');

    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(a).toHaveBeenLastCalledWith('tick');
    expect(b).toHaveBeenLastCalledWith('tick');
    expect(a).toHaveBeenCalledTimes(2);
    expect(b).toHaveBeenCalledTimes(2);
  });

  it('长周期任务按节拍取整，只在到期的节拍运行', () => {
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: 4 * SCHEDULER_TICK_MS });
    vi.advanceTimersByTime(3 * SCHEDULER_TICK_MS);
    expect(run).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('不立即跑的任务从登记时计周期，第一个节拍不提前触发', () => {
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: 4 * SCHEDULER_TICK_MS, runOnStart: false });
    vi.advanceTimersByTime(3 * SCHEDULER_TICK_MS);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(run).toHaveBeenCalledWith('tick');
  });

  it('页面在后台时节拍不跑，转入后台时通知一次，回到前台立即补跑', () => {
    const run = vi.fn();
    const onHidden = vi.fn();
    registerBackgroundTask(run, { intervalMs: SCHEDULER_TICK_MS, onHidden });
    run.mockClear();

    setVisibility('hidden');
    window.dispatchEvent(new Event('pagehide'));
    expect(onHidden).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3 * SCHEDULER_TICK_MS);
    expect(run).not.toHaveBeenCalled();

    setVisibility('visible');
    expect(run).toHaveBeenCalledTimes(1);
    expect(run).toHaveBeenCalledWith('resume');
  });

  it('focus、visibilitychange、online 在短窗口内合并为一次补跑', () => {
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: SCHEDULER_TICK_MS });
    run.mockClear();

    window.dispatchEvent(new Event('focus'));
    setVisibility('visible');
    window.dispatchEvent(new Event('online'));
    expect(run).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(2_000);
    window.dispatchEvent(new Event('focus'));
    expect(run).toHaveBeenCalledTimes(2);
  });

  it('runOnResume 为 false 的任务不随回到前台补跑', () => {
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: SCHEDULER_TICK_MS, runOnResume: false });
    run.mockClear();
    window.dispatchEvent(new Event('focus'));
    expect(run).not.toHaveBeenCalled();
  });

  it('minGapMs 内的补跑被跳过', () => {
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: 20 * SCHEDULER_TICK_MS, minGapMs: 3_000 });
    run.mockClear();
    window.dispatchEvent(new Event('focus'));
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(3_000);
    window.dispatchEvent(new Event('focus'));
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('isEnabled 返回 false 时本轮跳过', () => {
    let enabled = false;
    const run = vi.fn();
    registerBackgroundTask(run, { intervalMs: SCHEDULER_TICK_MS, isEnabled: () => enabled });
    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(run).not.toHaveBeenCalled();
    enabled = true;
    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('单个任务抛错不影响同一节拍的其他任务', () => {
    const broken = vi.fn(() => { throw new Error('boom'); });
    const healthy = vi.fn();
    registerBackgroundTask(broken, { intervalMs: SCHEDULER_TICK_MS });
    registerBackgroundTask(healthy, { intervalMs: SCHEDULER_TICK_MS });
    vi.advanceTimersByTime(SCHEDULER_TICK_MS);
    expect(healthy).toHaveBeenCalledTimes(2);
  });

  it('最后一个任务注销后卸下定时器与监听', () => {
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const run = vi.fn();
    const unregisterA = registerBackgroundTask(run, { intervalMs: SCHEDULER_TICK_MS });
    const unregisterB = registerBackgroundTask(vi.fn(), { intervalMs: SCHEDULER_TICK_MS });
    unregisterA();
    expect(clearIntervalSpy).not.toHaveBeenCalled();
    unregisterB();
    expect(clearIntervalSpy).toHaveBeenCalledTimes(1);
    expect(removeSpy).toHaveBeenCalledWith('focus', expect.any(Function));
    run.mockClear();
    vi.advanceTimersByTime(2 * SCHEDULER_TICK_MS);
    window.dispatchEvent(new Event('focus'));
    expect(run).not.toHaveBeenCalled();
  });
});
