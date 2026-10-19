'use client';

import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';
import type { AssistantActivity } from './assistantActivity';
import {
  isAwaitingModelOutput,
  MODEL_SLOW_AFTER_S,
  resolveActivityStatus,
  resolveSlowModelStatus,
  type ActivityStatusView,
  type ResolveActivityStatusOptions,
} from './activityStatusView';
import styles from './MessageAuxiliary.module.css';

/** 阶段持续超过该秒数才显示计时，避免短阶段闪烁数字。 */
const ELAPSED_VISIBLE_AFTER_S = 3;

interface AssistantActivityStatusProps extends ResolveActivityStatusOptions {
  activity: AssistantActivity;
  /**
   * 进行中的状态放在回答底部，跟随新追加的卡片与结果，用户视线不用回到顶部；
   * 失败、停止、工具问题等结论性状态留在顶部。缺省时不区分位置。
   */
  placement?: 'top' | 'bottom';
  /**
   * 模型输出进度的标记，任何新输出（正文、思考、草稿、阶段切换）都会改变它。
   * 传入后，模型阶段超过一定时长没有新输出时改为提示“模型响应变慢”。
   */
  outputMark?: string;
  className?: string;
}

export default function AssistantActivityStatus({
  activity,
  placement,
  outputMark,
  className,
  ...options
}: AssistantActivityStatusProps) {
  const watchingOutput = outputMark !== undefined && !options.stopPending && isAwaitingModelOutput(activity.kind);
  const silentS = useSecondsWithoutOutput(watchingOutput, outputMark);
  const view = silentS >= MODEL_SLOW_AFTER_S
    ? resolveSlowModelStatus(silentS)
    : resolveActivityStatus(activity, options);
  if (!view) return null;
  if (placement === 'top' && view.busy) return null;
  if (placement === 'bottom' && !view.busy) return null;
  return <StatusBar key={view.key} view={view} className={className} />;
}

function StatusBar({ view, className }: { view: ActivityStatusView; className?: string }) {
  const Icon = view.icon;
  const elapsedS = useElapsedSeconds(view.busy);

  return (
    <div
      role={view.role}
      aria-live={view.role === 'alert' ? 'assertive' : 'polite'}
      aria-atomic="true"
      data-tone={view.tone}
      className={cn('flex min-w-0 items-center gap-2', styles.activity, className)}
    >
      <span className={styles.statusIcon} aria-hidden="true">
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span className="max-w-full shrink-0 truncate font-medium">{view.title}</span>
        {view.detail ? (
          // 失败原因要完整读到（如「请换用其他模型」），换行而不截断；进行中的状态保持单行。
          <span className={cn('min-w-0 text-muted-foreground', view.tone === 'danger' ? 'break-words' : 'truncate')}>
            {view.detail}
          </span>
        ) : null}
      </span>
      {view.busy && view.key !== 'model_slow' && elapsedS >= ELAPSED_VISIBLE_AFTER_S ? (
        // 计时不进入读屏播报，避免每秒打断。
        <span className="shrink-0 tabular-nums text-muted-foreground" aria-hidden="true">
          {elapsedS} 秒
        </span>
      ) : null}
      {view.busy ? (
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
}

/** 当前阶段已持续的秒数；阶段切换时组件按 key 重新挂载，计时随之归零。 */
function useElapsedSeconds(active: boolean): number {
  const [startedAt] = useState(() => Date.now());
  const [elapsedS, setElapsedS] = useState(0);

  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => {
      setElapsedS(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);

  return elapsedS;
}

/** 距上次模型输出的秒数；输出标记变化即重新计时，未在等待模型输出时为 0。 */
function useSecondsWithoutOutput(watching: boolean, outputMark: string | undefined): number {
  const [silent, setSilent] = useState({ mark: outputMark, seconds: 0 });

  useEffect(() => {
    if (!watching) return undefined;
    const since = Date.now();
    const timer = window.setInterval(() => {
      setSilent({ mark: outputMark, seconds: Math.floor((Date.now() - since) / 1000) });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [watching, outputMark]);

  // 标记已变但计时器还没走到下一秒时，旧的秒数作废。
  return watching && silent.mark === outputMark ? silent.seconds : 0;
}
