'use client';

import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import { Activity, Clock, Wrench } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { AdminAgentRunRecord, AdminJsonValue, AdminToolCallRecord } from '@/types/adminAudit';
import { AdminAgentStatus } from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

const TOOL_STATUS_LABELS: Record<string, string> = { success: '成功', degraded: '部分可用', failed: '失败' };
const LIMIT_REASON_LABELS: Record<string, string> = {
  max_steps: '达到步数上限',
  max_tool_calls: '达到工具调用上限',
  timeout: '运行超时',
  output_length: '达到输出长度上限',
};

interface AdminExecutionInspectorProps {
  runs: AdminAgentRunRecord[];
  toolCalls: AdminToolCallRecord[];
}

export default function AdminExecutionInspector({ runs, toolCalls }: AdminExecutionInspectorProps) {
  const { t } = useTranslation();
  if (runs.length === 0 && toolCalls.length === 0) return null;

  return (
    <div className={styles.executionList}>
      {runs.map(run => (
        <section key={run.id} className={styles.executionCard}>
          <header className={styles.executionHeader}>
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Activity className="h-4 w-4 shrink-0" aria-hidden="true" />
              {run.turn_index ? `第 ${run.turn_index} 轮` : 'Agent 运行'}
            </h3>
            <AdminAgentStatus status={run.status} />
          </header>
          <div className={styles.executionId}><span>{t('admin.details.runId')}</span><code>{run.id}</code></div>
          <RunModel requested={run.model_id} answered={run.answer_model_id} />
          <dl className={styles.executionMetrics}>
            <div><dt>步骤</dt><dd>{run.total_steps}</dd></div>
            <div><dt>工具调用</dt><dd>{run.total_tool_calls}</dd></div>
            <div><dt>{t('admin.details.duration')}</dt><dd>{formatDuration(run.total_duration_ms)}</dd></div>
          </dl>
          {run.limit_reason ? <p className="mt-2 break-words text-xs text-muted-foreground">{LIMIT_REASON_LABELS[run.limit_reason] ?? run.limit_reason}</p> : null}
          {/* 单步且没调工具的运行，步骤明细和上面的汇总完全重复，不再展开。 */}
          {run.steps.length > 1 || run.steps.some(step => step.tool_calls.length > 0) ? (
            <ol className={styles.executionSteps}>
              {run.steps.map(step => (
                <li key={step.id} className={styles.executionStep}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">步骤 {step.step_number}</span>
                    <span className="flex items-center gap-2 text-muted-foreground"><AdminAgentStatus status={step.status} />{formatDuration(step.duration_ms)}</span>
                  </div>
                  {step.tool_calls.length > 0 ? (
                    <div className="mt-1 break-words text-muted-foreground">
                      关联工具：{step.tool_calls.map(call => `${call.tool_name}（${toolStatusLabel(call.status)}）`).join('、')}
                    </div>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
          {/* 每次运行的上限配置都一样，只在真的触发限制时才有排查价值。 */}
          {hasProgressContent(run.progress) || (run.limit_reason && run.config) || run.error ? <div className={styles.diagnosticGroup}>
            {hasProgressContent(run.progress) ? <SafeJson title="运行进度" value={run.progress} /> : null}
            {run.limit_reason && run.config ? <SafeJson title="运行上限配置" value={run.config} /> : null}
            {run.error ? <SafeJson title="运行错误" value={run.error} /> : null}
          </div> : null}
        </section>
      ))}

      {toolCalls.length > 0 ? (
        <section className={styles.executionCard}>
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Wrench className="h-4 w-4" aria-hidden="true" />
            工具调用
          </h3>
          <div className="mt-3 space-y-3">
            {toolCalls.map(call => (
              <article key={call.id} className={styles.executionStep}>
                <header className={styles.executionHeader}>
                  <h4 className="min-w-0 break-all text-sm font-medium">{call.tool_name}</h4>
                  <Badge variant="outline" className="max-w-full whitespace-normal break-all" title={call.status}>{toolStatusLabel(call.status)}</Badge>
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    {formatDuration(call.duration_ms)}
                  </span>
                </header>
                <div className={styles.diagnosticGroup}>
                  <SafeJson title="调用参数" value={call.arguments} />
                  <SafeJson title="返回结果" value={call.result_preview} />
                  {call.error ? <SafeJson title="工具错误" value={call.error} /> : null}
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function RunModel({ requested, answered }: { requested: string | null; answered: string | null }) {
  if (!requested && !answered) return null;
  const switched = requested && answered && requested !== answered;
  return (
    <div className="mt-2 min-w-0 break-all text-xs text-muted-foreground">
      {switched ? `请求 ${requested}，实际由 ${answered} 回答` : answered || requested}
    </div>
  );
}

function toolStatusLabel(status: string): string {
  return TOOL_STATUS_LABELS[status] ?? status;
}

/** 没调工具、没有计划时进度投影只剩空壳字段，展开也没有信息。 */
function hasProgressContent(progress: AdminJsonValue): boolean {
  if (!progress || typeof progress !== 'object' || Array.isArray(progress)) return false;
  const { progress: phase, plan, tool_digests: toolDigests, evidence } = progress;
  return Boolean(phase || plan || (Array.isArray(toolDigests) && toolDigests.length) || (Array.isArray(evidence) && evidence.length));
}

function SafeJson({ title, value }: { title: string; value: AdminJsonValue }) {
  return (
    <details className={styles.rawDetails}>
      <summary className="cursor-pointer text-xs text-muted-foreground">{title}</summary>
      <pre className={styles.rawContent}>
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function formatDuration(value: number | null): string {
  if (value === null) return '耗时未知';
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}
