/**
 * 后台周期任务的共享调度器：全局只有一个定时器和一套 focus / visibilitychange / online 监听。
 *
 * 各业务登记任务，而不是各自 setInterval + 各自监听。好处：
 * - 所有任务对齐到同一个节拍，同一轮触发的任务可以合并（为后续聚合请求铺路）；
 * - 页面在后台时统一不跑，切回前台时统一补跑一次（focus 与 visibilitychange 常同时触发，合并成一次）；
 * - 新的周期任务直接登记即可，不用再写一遍监听与清理。
 *
 * 只管「何时跑」，不管请求本身：去重、取消、失败重试仍由任务自己负责。
 */

/** 全局节拍。任务间隔按节拍取整：间隔短于节拍的任务按节拍跑。 */
export const SCHEDULER_TICK_MS = 15_000;
/** focus / visibilitychange / online 在这个窗口内只算一次「回到前台」。 */
const RESUME_COALESCE_MS = 1_000;

export type BackgroundTaskTrigger = 'start' | 'tick' | 'resume';

export interface BackgroundTaskOptions {
  /** 周期，毫秒。节拍到来时距上次运行已过「周期减半个节拍」就跑，保证与节拍对齐。 */
  intervalMs: number;
  /** 登记后立即跑一次（页面可见时）。默认 true；为 false 时首次周期运行在登记一个周期之后。 */
  runOnStart?: boolean;
  /** 回到前台（focus、变为可见、恢复联网）时立即跑一次。默认 true。 */
  runOnResume?: boolean;
  /** 两次运行的最小间隔，用于给回到前台的补跑去抖。默认 0。 */
  minGapMs?: number;
  /** 每次运行前判断，返回 false 时本轮跳过（例如没有进行中的对话就不轮询）。 */
  isEnabled?: () => boolean;
  /** 页面转入后台或即将卸载（pagehide）时调用，用于撤掉心跳一类的在线标记。 */
  onHidden?: () => void;
}

interface Task {
  run: (trigger: BackgroundTaskTrigger) => void;
  options: Required<Pick<BackgroundTaskOptions, 'intervalMs' | 'runOnStart' | 'runOnResume' | 'minGapMs'>>
    & Pick<BackgroundTaskOptions, 'isEnabled' | 'onHidden'>;
  /** 上次实际运行的时间，用于 minGapMs 去抖。 */
  lastRunAt: number | null;
  /** 周期起点：上次运行时间，或不立即跑的任务的登记时间。 */
  periodStartAt: number | null;
}

const tasks = new Set<Task>();
let tickTimer: ReturnType<typeof setInterval> | null = null;
let lastResumeAt = Number.NEGATIVE_INFINITY;
let wasVisible = true;

function isPageVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState === 'visible';
}

function runTask(task: Task, trigger: BackgroundTaskTrigger, now: number) {
  if (!tasks.has(task)) return;
  if (task.options.isEnabled && !task.options.isEnabled()) return;
  if (task.lastRunAt !== null && now - task.lastRunAt < task.options.minGapMs) return;
  task.lastRunAt = now;
  task.periodStartAt = now;
  try {
    task.run(trigger);
  } catch {
    // 单个任务出错不能影响同一节拍的其他任务；任务自己负责上报。
  }
}

function handleTick() {
  if (!isPageVisible()) return;
  const now = Date.now();
  for (const task of [...tasks]) {
    const due = task.periodStartAt === null
      || now - task.periodStartAt >= task.options.intervalMs - SCHEDULER_TICK_MS / 2;
    if (due) runTask(task, 'tick', now);
  }
}

function handleResume() {
  if (!isPageVisible()) return;
  const now = Date.now();
  if (now - lastResumeAt < RESUME_COALESCE_MS) return;
  lastResumeAt = now;
  for (const task of [...tasks]) {
    if (task.options.runOnResume) runTask(task, 'resume', now);
  }
}

function notifyHidden() {
  for (const task of [...tasks]) {
    try {
      task.options.onHidden?.();
    } catch {
      // 同 runTask。
    }
  }
}

function handleVisibilityChange() {
  const visible = isPageVisible();
  if (visible) {
    wasVisible = true;
    handleResume();
    return;
  }
  // 隐藏只通知一次，pagehide 之后紧跟的 visibilitychange 不重复撤标记。
  if (!wasVisible) return;
  wasVisible = false;
  notifyHidden();
}

function handlePageHide() {
  if (!wasVisible) return;
  wasVisible = false;
  notifyHidden();
}

function attach() {
  wasVisible = isPageVisible();
  tickTimer = setInterval(handleTick, SCHEDULER_TICK_MS);
  window.addEventListener('focus', handleResume);
  window.addEventListener('online', handleResume);
  window.addEventListener('pagehide', handlePageHide);
  document.addEventListener('visibilitychange', handleVisibilityChange);
}

function detach() {
  if (tickTimer !== null) clearInterval(tickTimer);
  tickTimer = null;
  window.removeEventListener('focus', handleResume);
  window.removeEventListener('online', handleResume);
  window.removeEventListener('pagehide', handlePageHide);
  document.removeEventListener('visibilitychange', handleVisibilityChange);
  lastResumeAt = Number.NEGATIVE_INFINITY;
}

/** 登记一个后台周期任务，返回注销函数。 */
export function registerBackgroundTask(
  run: (trigger: BackgroundTaskTrigger) => void,
  options: BackgroundTaskOptions,
): () => void {
  const task: Task = {
    run,
    options: {
      intervalMs: options.intervalMs,
      runOnStart: options.runOnStart ?? true,
      runOnResume: options.runOnResume ?? true,
      minGapMs: options.minGapMs ?? 0,
      isEnabled: options.isEnabled,
      onHidden: options.onHidden,
    },
    lastRunAt: null,
    // 不立即跑的任务从登记时开始计周期，避免第一个节拍就提前触发。
    periodStartAt: options.runOnStart === false ? Date.now() : null,
  };
  if (tasks.size === 0 && typeof window !== 'undefined') attach();
  tasks.add(task);
  if (task.options.runOnStart && isPageVisible()) runTask(task, 'start', Date.now());
  return () => {
    if (!tasks.delete(task)) return;
    if (tasks.size === 0 && typeof window !== 'undefined') detach();
  };
}

/** 仅供测试：清空任务并卸下监听。 */
export function resetBackgroundSchedulerForTests() {
  tasks.clear();
  if (typeof window !== 'undefined') detach();
  wasVisible = true;
}
