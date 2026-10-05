'use client';

import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import { Activity, Clock, Wrench } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { AdminAgentRunRecord, AdminJsonValue, AdminToolCallRecord } from '@/types/adminAudit';
import styles from './AdminSurface.module.css';

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
              Agent 运行
            </h3>
            <Badge variant="outline" className="max-w-full whitespace-normal break-all">{run.status}</Badge>
          </header>
          <div className={styles.executionId}><span>{t('admin.details.runId')}</span><code>{run.id}</code></div>
          {run.model_id || run.provider ? (
            <div className="mt-2 flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {run.model_id ? <span className="min-w-0 break-all">{run.model_id}</span> : null}
              {run.provider ? <span className="min-w-0 break-all">{run.provider}</span> : null}
            </div>
          ) : null}
          <dl className={styles.executionMetrics}>
            <div><dt>步骤</dt><dd>{run.total_steps}</dd></div>
            <div><dt>工具调用</dt><dd>{run.total_tool_calls}</dd></div>
            <div><dt>{t('admin.details.duration')}</dt><dd>{formatDuration(run.total_duration_ms)}</dd></div>
          </dl>
          {run.limit_reason ? <p className="mt-2 break-words text-xs text-muted-foreground">限制：{run.limit_reason}</p> : null}
          {run.steps.length > 0 ? (
            <ol className={styles.executionSteps}>
              {run.steps.map(step => (
                <li key={step.id} className={styles.executionStep}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">步骤 {step.step_number}</span>
                    <span className="text-muted-foreground">{step.status} · {formatDuration(step.duration_ms)}</span>
                  </div>
                  {step.tool_calls.length > 0 ? (
                    <div className="mt-1 break-words text-muted-foreground">
                      关联工具：{step.tool_calls.map(call => `${call.tool_name}（${call.status}）`).join('、')}
                    </div>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
          {run.progress || run.config || run.error ? <div className={styles.diagnosticGroup}>
            {run.progress ? <SafeJson title="进度安全投影" value={run.progress} /> : null}
            {run.config ? <SafeJson title="安全配置" value={run.config} /> : null}
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
                  <Badge variant="outline" className="max-w-full whitespace-normal break-all">{call.status}</Badge>
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    {formatDuration(call.duration_ms)}
                  </span>
                </header>
                <div className={styles.diagnosticGroup}>
                  <SafeJson title="参数安全投影" value={call.arguments} />
                  <SafeJson title="结果安全投影" value={call.result_preview} />
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
