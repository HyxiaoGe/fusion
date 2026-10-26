"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Brain, Loader2, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import ConfirmDialog from "@/components/ui/confirm-dialog";
import { Switch } from "@/components/ui/switch";
import styles from "@/components/settings/SettingsSurface.module.css";
import { clearMemories, deleteMemory, getMemories, setMemoryEnabled, type MemoryItem } from "@/lib/api/memories";
import { track } from "@/lib/analytics/analytics";

const RULE_KEYS = ["when", "keep", "skip", "use", "control"] as const;
// 与后端 MEMORY_CATEGORIES 的展示顺序一致；整理时新增的分类排在最后、显示原名。
const CATEGORY_ORDER = ["name", "residence", "workplace", "transport", "occupation", "health", "diet", "family", "interests", "response_style", "other"];

type LoadState = "loading" | "ready" | "error";

function groupByCategory(items: MemoryItem[]): Array<[string, MemoryItem[]]> {
  const groups = new Map<string, MemoryItem[]>();
  for (const item of items) {
    const group = groups.get(item.category);
    if (group) group.push(item);
    else groups.set(item.category, [item]);
  }
  const rank = (category: string) => {
    const index = CATEGORY_ORDER.indexOf(category);
    return index === -1 ? CATEGORY_ORDER.length : index;
  };
  return [...groups.entries()].sort(([left], [right]) => rank(left) - rank(right));
}

export default function MemoryPanel() {
  const { t, i18n } = useTranslation();
  const switchId = useId();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [enabled, setEnabled] = useState(true);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [toggling, setToggling] = useState(false);
  const [deletingIds, setDeletingIds] = useState<ReadonlySet<string>>(new Set());
  const [clearing, setClearing] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadControllerRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    setLoadState("loading");
    try {
      const overview = await getMemories(controller.signal);
      if (controller.signal.aborted) return;
      setEnabled(overview.enabled);
      setItems(overview.items);
      setLoadState("ready");
      track('memory_viewed', { enabled: overview.enabled, memory_count: overview.items.length });
    } catch {
      if (!controller.signal.aborted) setLoadState("error");
    }
  }, []);

  useEffect(() => {
    void load();
    return () => loadControllerRef.current?.abort();
  }, [load]);

  const groups = useMemo(() => groupByCategory(items), [items]);
  const dateFormatter = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, { year: "numeric", month: "2-digit", day: "2-digit" }),
    [i18n.language],
  );

  const handleToggle = async (next: boolean) => {
    if (toggling) return;
    setToggling(true);
    setError(null);
    try {
      const result = await setMemoryEnabled(next);
      setEnabled(result.enabled);
    } catch {
      setError(t("settings.memory.toggleFailed"));
    } finally {
      setToggling(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (deletingIds.has(id)) return;
    setDeletingIds((prev) => new Set(prev).add(id));
    setError(null);
    try {
      await deleteMemory(id);
      track('memory_deleted', { scope: 'single' });
      setItems((prev) => prev.filter((item) => item.id !== id));
    } catch {
      setError(t("settings.memory.deleteFailed"));
    } finally {
      setDeletingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleClear = async () => {
    if (clearing) return;
    setClearing(true);
    setError(null);
    try {
      await clearMemories();
      track('memory_deleted', { scope: 'all' });
      setItems([]);
    } catch {
      setError(t("settings.memory.clearFailed"));
    } finally {
      setClearing(false);
    }
  };

  const categoryLabel = (category: string) =>
    CATEGORY_ORDER.includes(category) ? t(`settings.memory.categories.${category}`) : category;

  const formatDate = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : dateFormatter.format(date);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="border-b pb-5">
          <CardTitle className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-primary" aria-hidden="true" />
            {t("settings.memory.title")}
          </CardTitle>
          <p className={styles.dialogDescription}>{t("settings.memory.description")}</p>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <label htmlFor={switchId} className="text-sm font-medium">{t("settings.memory.toggleLabel")}</label>
              <p className="text-xs text-muted-foreground">{t("settings.memory.toggleHint")}</p>
            </div>
            <Switch
              id={switchId}
              checked={enabled}
              disabled={loadState !== "ready" || toggling}
              onCheckedChange={(checked) => void handleToggle(checked)}
            />
          </div>

          <section aria-labelledby={`${switchId}-rules`} className="space-y-3">
            <h3 id={`${switchId}-rules`} className="text-sm font-medium">{t("settings.memory.rulesTitle")}</h3>
            <dl className="space-y-3">
              {RULE_KEYS.map((key) => (
                <div key={key} className="space-y-1">
                  <dt className="text-sm font-medium text-foreground/90">{t(`settings.memory.rules.${key}Title`)}</dt>
                  <dd className="text-sm leading-relaxed text-muted-foreground">{t(`settings.memory.rules.${key}`)}</dd>
                </div>
              ))}
            </dl>
          </section>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b pb-5">
          <div className="flex items-center justify-between gap-4">
            <CardTitle>{t("settings.memory.listTitle")}</CardTitle>
            {loadState === "ready" && items.length > 0 && (
              <Button type="button" variant="outline" size="sm" disabled={clearing} onClick={() => setConfirmOpen(true)}>
                {clearing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                {t("settings.memory.clearAll")}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {loadState === "loading" && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              {t("settings.memory.loading")}
            </p>
          )}
          {loadState === "error" && (
            <div className="flex items-center gap-3 text-sm" role="alert">
              <span className={styles.feedback} data-state="error">{t("settings.memory.loadFailed")}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                {t("settings.memory.retry")}
              </Button>
            </div>
          )}
          {loadState === "ready" && items.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("settings.memory.empty")}</p>
          )}
          {loadState === "ready" && groups.map(([category, groupItems]) => (
            <section key={category} aria-label={categoryLabel(category)} className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">{categoryLabel(category)}</h4>
              <ul className="space-y-2">
                {groupItems.map((item) => (
                  <li key={item.id} className="flex items-start justify-between gap-3 rounded-lg border px-3 py-2">
                    <div className="min-w-0 space-y-1">
                      <p className="text-sm break-words">{item.content}</p>
                      {formatDate(item.updated_at) && (
                        <p className="text-xs text-muted-foreground">
                          {t("settings.memory.updatedAt", { date: formatDate(item.updated_at) })}
                        </p>
                      )}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={t("settings.memory.deleteLabel", { content: item.content })}
                      disabled={deletingIds.has(item.id) || clearing}
                      onClick={() => void handleDelete(item.id)}
                    >
                      {deletingIds.has(item.id)
                        ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                        : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {error && <p className={styles.feedback} data-state="error" role="alert">{error}</p>}
        </CardContent>
      </Card>

      <ConfirmDialog
        isOpen={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => void handleClear()}
        title={t("settings.memory.clearTitle")}
        description={t("settings.memory.clearDescription")}
        confirmLabel={t("settings.memory.clearConfirm")}
        cancelLabel={t("settings.memory.cancel")}
        variant="destructive"
      />
    </div>
  );
}
