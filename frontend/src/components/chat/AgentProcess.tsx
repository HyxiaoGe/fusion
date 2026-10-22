'use client';

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowRightLeft, Brain, ChevronUp, Eye, Loader2, TriangleAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { modelFallbackReasonLabel } from '@/lib/agent/modelFallback';
import { RUN_STATUS_TREATMENT } from '@/lib/agent/statusTreatment';
import { getToolErrorDisplay } from '@/lib/agent/toolErrorDisplay';
import { describeToolArgument, getToolMeta } from '@/lib/agent/toolRegistry';
import type { TrajectoryBadgeStatus } from '@/lib/trajectory/TrajectoryCellProjection';
import { formatTrajectoryDuration } from '@/lib/trajectory/trajectoryCellPresentation';
import { cn } from '@/lib/utils';
import type { AgentModelFallback, AgentRunState, ToolCallState } from '@/types/agentRun';
import type { TrajectoryRunSummary } from '@/types/trajectory';
import {
  firstSentence,
  highestPriorityIssue,
  isAwaitingChoice,
  runDurationMs,
  runStatusLabel,
  toolCallDurationMs,
  type AgentProcessRow,
} from './agentProcessModel';
import { ReasoningMarkdown } from './ReasoningMarkdown';
import styles from './MessageAuxiliary.module.css';

interface AgentProcessProps {
  run: AgentRunState;
  rows: readonly AgentProcessRow[];
  /** 最后一段思考仍在输出，展开显示全文。 */
  thinkingStreaming: boolean;
  isVisible: boolean;
  onToggle: () => void;
  runSummary?: TrajectoryRunSummary;
  trajectoryStatus: TrajectoryBadgeStatus;
  onInspect?: () => void;
}

// 轨迹完整与否是诊断信息，只有记录不全时才在聊天页提示。
const TRAJECTORY_NOTICE: Partial<Record<TrajectoryBadgeStatus, string>> = {
  degraded: '轨迹记录不完整',
  truncated: '轨迹已截断',
};

/**
 * 一轮回答的过程：标题汇总整轮状态与耗时，展开后按发生顺序列出思考、工具调用和换模型。
 * 工具行不依赖思考内容，关掉思考的模型也能看到调用了什么。
 */
export default function AgentProcess({
  run,
  rows,
  thinkingStreaming,
  isVisible,
  onToggle,
  runSummary,
  trajectoryStatus,
  onInspect,
}: AgentProcessProps) {
  const { t } = useTranslation();
  const bodyId = useId();
  const running = run.status === 'running' && !run.stopConfirmation;
  const now = useNowWhile(running);
  const expanded = isVisible && rows.length > 0;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (running && expanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [rows, running, expanded]);

  const issue = useMemo(() => highestPriorityIssue(run), [run]);
  const duration = run.stopConfirmation ? null : formatTrajectoryDuration(runDurationMs(run, now, runSummary));
  const toolCount = rows.filter(row => row.kind === 'tool').length;
  const thinkingCount = rows.filter(row => row.kind === 'thinking').length;
  const meta = [
    duration ? `用时 ${duration}` : null,
    toolCount > 0 ? `${toolCount} 次工具调用` : null,
    thinkingCount > 0 ? `思考 ${thinkingCount} 段` : null,
  ].filter(Boolean).join(' · ');
  const treatment = RUN_STATUS_TREATMENT[run.status];
  const StatusIcon = running ? Loader2 : treatment.icon;
  const notice = TRAJECTORY_NOTICE[trajectoryStatus];
  const lastRowId = rows.at(-1)?.id;

  const title = (
    <span className="flex min-w-0 flex-1 items-center gap-2">
      <span className={styles.statusIcon} data-done={running ? undefined : 'true'} data-tone={treatment.color} aria-hidden="true">
        <StatusIcon className={cn('h-3.5 w-3.5', running && 'animate-spin motion-reduce:animate-none')} />
      </span>
      <span className="shrink-0 font-medium text-foreground">{runStatusLabel(run)}</span>
      {meta ? <span className="min-w-0 truncate">{meta}</span> : null}
      {issue ? (
        <span className="inline-flex min-w-0 items-center gap-1 text-warn">
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate">{issue}</span>
        </span>
      ) : null}
    </span>
  );

  return (
    <section
      aria-label="Agent 运行过程"
      className={cn(styles.reasoning, running && styles.streaming)}
    >
      <div className={styles.processHeader}>
        {rows.length > 0 ? (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={bodyId}
            onClick={onToggle}
            className={styles.header}
          >
            {title}
            <span className={styles.chevron} aria-hidden="true">
              <ChevronUp className={cn('h-3.5 w-3.5 transition-transform duration-200', !expanded && 'rotate-180')} />
            </span>
          </button>
        ) : (
          <div className={styles.header}>{title}</div>
        )}
        {notice || onInspect ? (
          <span className={styles.trajectoryDetails}>
            {notice ? <span className={cn(styles.badge, 'border border-warn/30 bg-warn/5 font-medium text-warn')}>{notice}</span> : null}
            {onInspect ? (
              <Button type="button" variant="ghost" size="sm" className={styles.detailButton} onClick={onInspect}>
                <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                {t('chatBody.trajectory.inspect')}
              </Button>
            ) : null}
          </span>
        ) : null}
      </div>

      {rows.length > 0 ? (
        <div
          id={bodyId}
          aria-hidden={!expanded}
          inert={!expanded}
          className={cn(styles.details, expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}
        >
          <div className="overflow-hidden">
            <div className={styles.detailsInner}>
              <div ref={scrollRef} className={styles.processBody}>
                <ol className={styles.processRows}>
                  {rows.map(row => {
                    if (row.kind === 'thinking') {
                      return (
                        <ThinkingRow
                          key={row.id}
                          text={row.text}
                          streaming={thinkingStreaming && row.id === lastRowId}
                        />
                      );
                    }
                    if (row.kind === 'tool') return <ToolRow key={row.id} call={row.call} />;
                    return <FallbackRow key={row.id} fallback={row.fallback} />;
                  })}
                </ol>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function useNowWhile(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function ThinkingRow({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const expanded = open || streaming;
  return (
    <li className={styles.processRow}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        disabled={streaming}
        onClick={() => setOpen(value => !value)}
        className={styles.processRowButton}
      >
        <Brain className={cn('h-3.5 w-3.5 shrink-0', streaming && 'animate-pulse motion-reduce:animate-none')} aria-hidden="true" />
        <span className="shrink-0 font-medium">{streaming ? '思考中' : '思考'}</span>
        {expanded ? null : <span className="min-w-0 truncate text-muted-foreground">{firstSentence(text)}</span>}
      </button>
      {expanded ? (
        <div id={bodyId} className={cn(styles.content, styles.processRowDetail)}>
          <ReasoningMarkdown content={text} />
        </div>
      ) : null}
    </li>
  );
}

function toolStatusText(call: ToolCallState): { label: string; tone: 'muted' | 'warn' | 'danger' } | null {
  if (isAwaitingChoice(call)) return { label: '待确认地点', tone: 'warn' };
  switch (call.status) {
    case 'failed':
      return { label: '失败', tone: 'danger' };
    case 'degraded':
      return { label: '部分可用', tone: 'warn' };
    case 'interrupted':
      return { label: '已中断', tone: 'muted' };
    default:
      return null;
  }
}

function toolResultText(call: ToolCallState): string {
  if (call.status === 'running') return '执行中…';
  if (isAwaitingChoice(call)) return '地名有多个候选，等你选定后继续';
  const error = getToolErrorDisplay(call.toolName, call.status, call.error);
  if (error) return error;
  const summary = call.resultSummary;
  const count = summary?.count ?? summary?.result_count;
  const parts = [summary?.title, typeof count === 'number' ? `${count} 条结果` : null].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : '已完成';
}

function argumentText(value: unknown): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  if (!text) return '';
  return text.length > 300 ? `${text.slice(0, 299)}…` : text;
}

function ToolRow({ call }: { call: ToolCallState }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const meta = getToolMeta(call.toolName);
  const Icon = meta.icon;
  const summary = meta.summarize(call.arguments ?? {});
  const status = toolStatusText(call);
  const duration = formatTrajectoryDuration(toolCallDurationMs(call));
  const args = Object.entries(call.arguments ?? {})
    .map(([key, value]) => ({ key, ...describeToolArgument(call.toolName, key, argumentText(value)) }))
    .filter(({ value }) => value);

  return (
    <li className={styles.processRow}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(value => !value)}
        className={styles.processRowButton}
        data-status={call.status}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="shrink-0 font-medium">{meta.label}</span>
        {summary ? <span className="min-w-0 truncate text-muted-foreground">{summary}</span> : null}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {status ? <span data-tone={status.tone} className={styles.processStatus}>{status.label}</span> : null}
          {call.status === 'running' ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="执行中" />
          ) : duration ? (
            <span className="tabular-nums text-muted-foreground">{duration}</span>
          ) : null}
        </span>
      </button>
      {open ? (
        <dl id={bodyId} className={cn(styles.processRowDetail, styles.processFields)}>
          {args.map(({ key, label, value }) => (
            <div key={key} className={styles.processField}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
          <div className={styles.processField}>
            <dt>结果</dt>
            <dd>{toolResultText(call)}</dd>
          </div>
        </dl>
      ) : null}
    </li>
  );
}

function FallbackRow({ fallback }: { fallback: AgentModelFallback }) {
  const reason = modelFallbackReasonLabel(fallback.reason);
  return (
    <li className={styles.processRow}>
      <div className={styles.processRowButton} data-status="degraded">
        <ArrowRightLeft className="h-3.5 w-3.5 shrink-0 text-warn" aria-hidden="true" />
        <span className="shrink-0 font-medium">{fallback.fallbackModel ? '已换备用模型' : '没有可换的备用模型'}</span>
        <span className="min-w-0 truncate text-muted-foreground">
          {fallback.fallbackModel
            ? `${fallback.failedModel} → ${fallback.fallbackModel} · ${reason}`
            : `${fallback.failedModel} 限时重试 · ${reason}`}
        </span>
      </div>
    </li>
  );
}
