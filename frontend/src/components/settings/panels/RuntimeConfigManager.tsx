"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, Inbox, Loader2, RefreshCw, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { SettingsBadge, SettingsButton } from "@/components/settings/SettingsControls";
import { SettingsMonitoringState } from "@/components/settings/SettingsMonitoringState";
import styles from "@/components/settings/SettingsMonitoring.module.css";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchRuntimeConfigSnapshotAPI,
  type RuntimeConfigEntry,
  type RuntimeConfigPayload,
  type RuntimeConfigSnapshot,
} from "@/lib/api/runtimeConfig";
import {
  getRuntimeConfigPresentation,
  summarizeRuntimeConfigPayload,
} from "./runtimeConfigPresentation";

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function statusBadge(entry: RuntimeConfigEntry) {
  if (!entry.valid) {
    return <SettingsBadge tone="danger">校验失败</SettingsBadge>;
  }
  if (entry.is_active) {
    return <SettingsBadge tone="success">生效中</SettingsBadge>;
  }
  return <SettingsBadge>候选版本</SettingsBadge>;
}

function sourceLabel(source: string): string {
  return source === "db" ? "数据库" : "代码默认";
}

function PayloadSummary({ payload }: { payload: RuntimeConfigPayload }) {
  return (
    <ul className="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
      {summarizeRuntimeConfigPayload(payload).map((item) => (
        <li key={item} className={styles.summary}>
          {item}
        </li>
      ))}
    </ul>
  );
}

export default function RuntimeConfigManager() {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<RuntimeConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchRuntimeConfigSnapshotAPI();
      setSnapshot(data);
    } catch (err: unknown) {
      setError(errorMessage(err, "运行时配置加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const stats = useMemo(() => {
    const effectiveCount = snapshot?.effective.length ?? 0;
    const entryCount = snapshot?.entries.length ?? 0;
    const issueCount = [
      ...(snapshot?.effective ?? []).filter((item) => !item.valid || (item.skipped_versions?.length ?? 0) > 0),
      ...(snapshot?.entries ?? []).filter((entry) => !entry.valid),
    ].length;
    return { effectiveCount, entryCount, issueCount };
  }, [snapshot]);

  if (loading && !snapshot) {
    return (
      <SettingsMonitoringState title="运行时配置" icon={SlidersHorizontal} state="loading">
        {t("settings.monitoring.runtimeLoading", { defaultValue: "正在加载运行时配置" })}
      </SettingsMonitoringState>
    );
  }

  if (error && !snapshot) {
    return (
      <SettingsMonitoringState title="运行时配置" icon={SlidersHorizontal} state="error" action={
        <SettingsButton size="sm" variant="outline" onClick={() => void load()}>
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          {t("settings.monitoring.retry", { defaultValue: "重试" })}
        </SettingsButton>
      }>{error}</SettingsMonitoringState>
    );
  }

  return (
    <div className="space-y-4" aria-busy={loading}>
      <Card className={styles.card}>
        <CardHeader className={styles.header}>
          <div className="flex w-full flex-wrap items-center justify-between gap-3">
            <CardTitle className={styles.title}>
              <SlidersHorizontal className="h-5 w-5 text-info" />
              运行时配置
            </CardTitle>
            <SettingsButton size="sm" variant="outline" onClick={() => void load()} disabled={loading} aria-label={t("settings.monitoring.runtimeRefresh", { defaultValue: "刷新运行时配置" })}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
              {t(loading ? "settings.monitoring.refreshing" : "settings.monitoring.refresh", { defaultValue: loading ? "刷新中" : "刷新" })}
            </SettingsButton>
          </div>
        </CardHeader>
        <CardContent className={`grid gap-3 md:grid-cols-3 ${styles.body}`}>
          <div className={styles.stat} data-tone="info">
            <p className="text-xs text-muted-foreground">当前配置</p>
            <p className={`mt-2 ${styles.statValue}`}>{stats.effectiveCount}</p>
          </div>
          <div className={styles.stat}>
            <p className="text-xs text-muted-foreground">版本记录</p>
            <p className={`mt-2 ${styles.statValue}`}>{stats.entryCount}</p>
          </div>
          <div className={styles.stat} data-tone={stats.issueCount > 0 ? "warning" : undefined}>
            <p className="text-xs text-muted-foreground">需关注</p>
            <p className={`mt-2 ${styles.statValue}`}>{stats.issueCount}</p>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div role="alert" className={styles.notice} data-tone="danger">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <p>{error}</p>
            <p>{t("settings.monitoring.previousSnapshot", { defaultValue: "当前显示上次读取的配置，可再次刷新。" })}</p>
          </div>
        </div>
      )}

      <Card className={styles.card}>
        <CardHeader className={styles.header}>
          <CardTitle className="text-base">当前生效配置</CardTitle>
        </CardHeader>
        <CardContent className={`space-y-3 ${styles.body}`}>
          {snapshot?.effective.length === 0 && (
            <p className={styles.notice}><Inbox className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{t("settings.monitoring.noEffective", { defaultValue: "暂无生效配置" })}</p>
          )}
          {(snapshot?.effective ?? []).map((item) => {
            const presentation = getRuntimeConfigPresentation(item.namespace, item.key);
            return (
              <div key={`${item.namespace}:${item.key}`} className={styles.detail}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{presentation.title}</p>
                      <SettingsBadge>{presentation.category}</SettingsBadge>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">{presentation.description}</p>
                    <p className="mt-2 text-xs text-muted-foreground">{presentation.impact}</p>
                    <p className="mt-1 text-xs text-muted-foreground">内部标识：{item.namespace} / {item.key}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      当前来源：{sourceLabel(item.source)} · {item.version}
                    </p>
                  </div>
                  <SettingsBadge tone={item.valid ? "success" : "danger"}>{item.valid ? "有效" : "异常"}</SettingsBadge>
                </div>
                <PayloadSummary payload={item.payload} />
                {(item.skipped_versions?.length ?? 0) > 0 && (
                  <p className={`mt-3 ${styles.notice}`} data-tone="warning">跳过 {item.skipped_versions?.length} 个坏版本</p>
                )}
                {item.issues.length > 0 && (
                  <p className={`mt-3 ${styles.notice}`} data-tone="danger">{item.issues.join("；")}</p>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Card className={styles.card}>
        <CardHeader className={styles.header}>
          <div className="min-w-0 space-y-1">
            <CardTitle className={styles.title}>
              <ShieldCheck className="h-5 w-5 text-info" />
              配置版本记录
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              这里只展示运行时配置的当前状态和历史记录；配置变更仍通过代码、Agent 和 CI/CD 流程完成。
            </p>
          </div>
        </CardHeader>
        <CardContent className={`space-y-3 ${styles.body}`}>
          {snapshot?.entries.length === 0 && (
            <p className={styles.notice}><Inbox className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />{t("settings.monitoring.noVersions", { defaultValue: "暂无配置版本记录" })}</p>
          )}
          {(snapshot?.entries ?? []).map((entry) => {
            const presentation = getRuntimeConfigPresentation(entry.namespace, entry.key);
            return (
              <div
                key={entry.id}
                data-testid={`runtime-config-entry-${entry.id}`}
                className={styles.detail}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{presentation.title}</p>
                      {statusBadge(entry)}
                      <SettingsBadge>{presentation.category}</SettingsBadge>
                    </div>
                    <p className="mt-1 text-sm">{entry.version}</p>
                    <p className="mt-1 text-sm text-muted-foreground">{entry.description || presentation.description}</p>
                    <p className="mt-1 text-xs text-muted-foreground">内部标识：{entry.namespace} / {entry.key}</p>
                    <p className="mt-1 text-xs text-muted-foreground">更新于 {formatDateTime(entry.updated_at || entry.created_at)}</p>
                    <PayloadSummary payload={entry.payload} />
                    {entry.issues.length > 0 && (
                      <p className={`mt-3 ${styles.notice}`} data-tone="danger">{entry.issues.join("；")}</p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
