'use client';

import { useCallback, useState } from 'react';
import { Compass, ExternalLink, RefreshCw } from 'lucide-react';
import { getAdminRoutingQuality } from '@/lib/api/adminAudit';
import { buildAdminAuditUrl } from '@/lib/admin/adminAuditRoute';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { Button } from '@/components/ui/button';
import type {
  AdminRoutingQualityQuery,
  AdminRoutingQualityResponse,
  AdminRoutingQualitySignal,
} from '@/types/adminAudit';
import {
  ADMIN_WINDOW_OPTIONS,
  AdminEmpty,
  AdminError,
  AdminLoading,
  formatAdminDate,
  formatNumber,
  toShanghaiIso,
} from './AdminPanelPrimitives';
import styles from './AdminWindowGlass.module.css';

const SIGNAL_LABELS: Record<AdminRoutingQualitySignal, string> = {
  classifier_unavailable: '分类失败兜底',
  clarification_only: '判为仅澄清',
  tools_unavailable: '工具不可用',
  no_tool_call: '有工具未调用',
  web_only_fallback: '只用了联网',
  primary_tool_missed: '主工具未调用',
};
const SIGNAL_ORDER: AdminRoutingQualitySignal[] = [
  'classifier_unavailable',
  'no_tool_call',
  'web_only_fallback',
  'primary_tool_missed',
  'clarification_only',
  'tools_unavailable',
];
const STATUS_LABELS: Record<AdminRoutingQualityResponse['samples'][number]['status'], string> = {
  completed: '已完成',
  interrupted: '已中断',
  failed: '失败',
  running: '未结束',
};

interface RoutingQualityPanelProps {
  onForbidden: () => void;
}

export default function RoutingQualityPanel({ onForbidden }: RoutingQualityPanelProps) {
  const [windowHours, setWindowHours] = useState<number>(24);
  const [query, setQuery] = useState<AdminRoutingQualityQuery>(() => buildWindowQuery(24));
  const loader = useCallback(
    (signal: AbortSignal) => getAdminRoutingQuality(query, signal),
    [query],
  );
  const resource = useAdminAuditResource(loader, onForbidden);

  const applyWindow = (hours: number) => {
    setWindowHours(hours);
    setQuery(buildWindowQuery(hours));
  };

  return (
    <section aria-label="能力路由质量" className="mb-6 rounded-xl border border-border bg-card p-4">
      <header className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <Compass className="h-4 w-4 text-primary" aria-hidden="true" />
            能力路由质量
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            对比每轮路由结果与实际工具调用，信号是待复核的线索而非判错；工具类信号只统计已完成的运行。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => applyWindow(windowHours)} aria-label="刷新路由质量">
          <RefreshCw />刷新
        </Button>
      </header>

      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="路由质量统计时间窗口">
        {ADMIN_WINDOW_OPTIONS.map(option => (
          <Button
            key={option.hours}
            type="button"
            size="sm"
            variant="outline"
            className={styles.windowButton}
            aria-label={`路由质量${option.label}`}
            aria-pressed={windowHours === option.hours}
            onClick={() => applyWindow(option.hours)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <div className="mt-4">
        {resource.loading ? <AdminLoading /> : resource.error ? (
          <AdminError message={resource.error} onRetry={resource.reload} retryLabel="重新加载路由质量" />
        ) : resource.data ? (
          <RoutingQualityContent data={resource.data} />
        ) : null}
      </div>
    </section>
  );
}

function RoutingQualityContent({ data }: { data: AdminRoutingQualityResponse }) {
  if (data.summary.total === 0) {
    return <AdminEmpty>当前时间窗口内暂无带路由结果的运行</AdminEmpty>;
  }
  const { scope, summary } = data;
  const excluded = [
    scope.running_count > 0 ? `${formatNumber(scope.running_count)} 个未结束` : '',
    scope.interrupted_count > 0 ? `${formatNumber(scope.interrupted_count)} 个已中断` : '',
    scope.failed_count > 0 ? `${formatNumber(scope.failed_count)} 个失败` : '',
  ].filter(Boolean);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{formatAdminDate(scope.created_from)} 至 {formatAdminDate(scope.created_to)}</span>
        <span>
          {formatNumber(summary.total)} 次路由，{formatNumber(summary.completed)} 个已完成
          {excluded.length > 0 ? `；${excluded.join('、')}不计工具类信号` : ''}
          {scope.unrouted_count > 0 ? `；${formatNumber(scope.unrouted_count)} 个无路由记录` : ''}
        </span>
      </div>

      <section aria-label="路由信号总览" className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {SIGNAL_ORDER.map(signal => (
          <div key={signal} className="rounded-lg border border-border/70 bg-background/50 p-3">
            <div className="text-xs text-muted-foreground">{SIGNAL_LABELS[signal]}</div>
            <div className={`mt-1 text-lg font-semibold ${summary.signals[signal] > 0 ? 'text-warn' : 'text-foreground'}`}>
              {formatNumber(summary.signals[signal])}
            </div>
          </div>
        ))}
      </section>

      <PackageTable items={data.by_package} />
      <SampleTable data={data} />
    </div>
  );
}

function PackageTable({ items }: { items: AdminRoutingQualityResponse['by_package'] }) {
  return (
    <section>
      <h3 className="mb-2 text-sm font-medium">按能力包</h3>
      <div className="overflow-x-auto rounded-lg border border-border/70">
        <table className="w-full min-w-[860px] text-left text-xs">
          <caption className="sr-only">按能力包统计路由信号</caption>
          <thead className="bg-muted/30 text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2">能力包</th>
              <th scope="col" className="px-3 py-2">路由 / 完成</th>
              {SIGNAL_ORDER.map(signal => (
                <th key={signal} scope="col" className="px-3 py-2">{SIGNAL_LABELS[signal]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map(item => (
              <tr key={item.package_id} className="border-t border-border/60">
                <td className="px-3 py-2 font-mono font-medium">{item.package_id}</td>
                <td className="px-3 py-2">{formatNumber(item.total)} / {formatNumber(item.completed)}</td>
                {SIGNAL_ORDER.map(signal => (
                  <td key={signal} className={`px-3 py-2 ${item.signals[signal] > 0 ? 'font-medium text-warn' : 'text-muted-foreground'}`}>
                    {formatNumber(item.signals[signal])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SampleTable({ data }: { data: AdminRoutingQualityResponse }) {
  const { samples, scope } = data;
  return (
    <section>
      <h3 className="mb-2 text-sm font-medium">
        可疑样本
        {scope.sample_total > samples.length ? (
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            共 {formatNumber(scope.sample_total)} 条，展示最新 {formatNumber(samples.length)} 条
          </span>
        ) : null}
      </h3>
      {samples.length === 0 ? <AdminEmpty>当前时间窗口内没有可疑样本</AdminEmpty> : (
        <div className="overflow-x-auto rounded-lg border border-border/70">
          <table className="w-full min-w-[860px] text-left text-xs">
            <caption className="sr-only">可疑路由样本</caption>
            <thead className="bg-muted/30 text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2">时间</th>
                <th scope="col" className="px-3 py-2">能力包</th>
                <th scope="col" className="px-3 py-2">信号</th>
                <th scope="col" className="px-3 py-2">实际调用</th>
                <th scope="col" className="px-3 py-2">状态</th>
                <th scope="col" className="px-3 py-2">会话</th>
              </tr>
            </thead>
            <tbody>
              {samples.map(sample => (
                <tr key={sample.run_id} className="border-t border-border/60">
                  <td className="whitespace-nowrap px-3 py-2">{formatAdminDate(sample.started_at)}</td>
                  <td className="px-3 py-2 font-mono">
                    {sample.package_id}
                    {sample.required_primary_tool_name ? (
                      <div className="text-muted-foreground">主工具 {sample.required_primary_tool_name}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">{sample.signals.map(signal => SIGNAL_LABELS[signal] ?? signal).join('、')}</td>
                  <td className="px-3 py-2 font-mono">{formatCalledTools(sample.called_tools)}</td>
                  <td className="px-3 py-2">{STATUS_LABELS[sample.status] ?? sample.status}</td>
                  <td className="px-3 py-2">
                    <a
                      href={buildAdminAuditUrl({ tab: 'conversations', conversationId: sample.conversation_id })}
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                      aria-label={`查看会话 ${sample.conversation_id}`}
                    >
                      查看<ExternalLink className="h-3 w-3" aria-hidden="true" />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function formatCalledTools(tools: string[]): string {
  if (tools.length === 0) return '无';
  // MCP 别名是服务端生成的不透明标识，对人工复核没有信息量。
  const labels = tools.map(tool => (tool.startsWith('mcp_') ? 'MCP' : tool));
  return Array.from(new Set(labels)).join(', ');
}

function buildWindowQuery(hours: number, now = new Date()): AdminRoutingQualityQuery {
  const createdFrom = new Date(now.getTime() - hours * 60 * 60 * 1000);
  return { created_from: toShanghaiIso(createdFrom), created_to: toShanghaiIso(now) };
}
