"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Eye,
  EyeOff,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { SettingsBadge as Badge, SettingsButton as Button, SettingsInput as Input, SettingsSelectTrigger as SelectTrigger, SettingsSelectContent as SelectContent, SettingsSelectItem as SelectItem } from "@/components/settings/SettingsControls";
import { useSettingsDialogFocus } from "@/components/settings/useSettingsDialogFocus";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import ProviderIcon from "@/components/models/ProviderIcon";
import {
  Select,
} from "@/components/ui/select";
import {
  fetchModelManagementSnapshotAPI,
  updateModelVisibilityAPI,
} from "@/lib/api/modelManagement";
import { isAdminAccessError } from "@/lib/admin/adminAccess";
import { refreshModels } from "@/lib/config/modelConfig";
import { useAppDispatch } from "@/redux/hooks";
import { updateModels, updateProviders } from "@/redux/slices/modelsSlice";
import type {
  ModelManagementRegisteredModel,
  ModelManagementSnapshot,
} from "@/types/modelManagement";

const ALL_PROVIDERS_VALUE = "__all_providers__";
const UNKNOWN_PROVIDER_VALUE = "__unknown_provider__";
interface ProviderCategory {
  id: string;
  label: string;
  registeredCount: number;
}

interface ManagementAction {
  model: ModelManagementRegisteredModel;
  nextSelectable: boolean;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function providerId(value?: string | null): string {
  return value?.trim().toLowerCase() || UNKNOWN_PROVIDER_VALUE;
}

function providerLabel(value?: string | null, fallback?: string | null): string {
  return value?.trim() || fallback?.trim() || "未记录提供商";
}

function matchesModelSearch(query: string, values: Array<string | null | undefined>): boolean {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = values.filter(Boolean).join(" ").toLocaleLowerCase();
  return terms.every((term) => haystack.includes(term));
}

function registeredStateLabel(model: ModelManagementRegisteredModel): string {
  if (!model.selectable) return "已隐藏";
  if (!model.routable) return "不可路由";
  return model.state === "active" || model.state === "selectable" ? "可选择" : model.state;
}

function healthLabel(health: ModelManagementRegisteredModel["health"]): string {
  const status = modelHealthStatus(health);
  const labels: Record<string, string> = {
    healthy: "健康",
    unhealthy: "异常",
    unknown: "待探测",
  };
  return status ? (labels[status] ?? status) : "未知";
}

function modelHealthStatus(health: ModelManagementRegisteredModel["health"]): string | undefined {
  return typeof health === "string" ? health : health?.status;
}

function registeredModelIsUnhealthy(model: ModelManagementRegisteredModel): boolean {
  return modelHealthStatus(model.health) === "unhealthy";
}

function registeredModelIsSelectable(model: ModelManagementRegisteredModel): boolean {
  return model.selectable && model.routable && !registeredModelIsUnhealthy(model);
}

export default function ModelManagementPanel() {
  const dispatch = useAppDispatch();
  const [snapshot, setSnapshot] = useState<ModelManagementSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [action, setAction] = useState<ManagementAction | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const actionFocus = useSettingsDialogFocus({ open: action !== null, fallbackRef: panelRef });
  const [reason, setReason] = useState("");
  const [selectedProvider, setSelectedProvider] = useState(ALL_PROVIDERS_VALUE);
  const [searchQuery, setSearchQuery] = useState("");
  const snapshotRequestIdRef = useRef(0);
  const activeSnapshotRequestRef = useRef<{
    id: number;
    promise: Promise<ModelManagementSnapshot | null>;
  } | null>(null);

  const denyAccess = useCallback(() => {
    setAccessDenied(true);
    setSnapshot(null);
    setError(null);
    setNotice(null);
  }, []);

  const loadSnapshot = useCallback((
    showLoading = false,
    supersedeActiveRequest = false,
    clearVisibleError = true,
  ): Promise<ModelManagementSnapshot | null> => {
    if (!supersedeActiveRequest && activeSnapshotRequestRef.current) {
      return activeSnapshotRequestRef.current.promise;
    }

    const requestId = snapshotRequestIdRef.current + 1;
    snapshotRequestIdRef.current = requestId;
    if (showLoading) setLoading(true);
    if (clearVisibleError) setError(null);
    const request = (async (): Promise<ModelManagementSnapshot | null> => {
      try {
        const nextSnapshot = await fetchModelManagementSnapshotAPI();
        if (requestId !== snapshotRequestIdRef.current) return null;
        setSnapshot(nextSnapshot);
        setAccessDenied(false);
        return nextSnapshot;
      } catch (caught: unknown) {
        if (requestId !== snapshotRequestIdRef.current) return null;
        if (isAdminAccessError(caught)) {
          denyAccess();
          return null;
        }
        if (clearVisibleError) {
          setError(errorMessage(caught, "模型管理数据加载失败"));
        }
        return null;
      } finally {
        if (activeSnapshotRequestRef.current?.id === requestId) {
          activeSnapshotRequestRef.current = null;
        }
        if (requestId === snapshotRequestIdRef.current) {
          setLoading(false);
        }
      }
    })();
    activeSnapshotRequestRef.current = { id: requestId, promise: request };
    return request;
  }, [denyAccess]);

  useEffect(() => {
    void loadSnapshot(true);
  }, [loadSnapshot]);

  const providerCategories = useMemo<ProviderCategory[]>(() => {
    const categories = new Map<string, ProviderCategory>();
    const ensureCategory = (id: string, label: string): ProviderCategory => {
      const current = categories.get(id);
      if (current) {
        if (current.label === id && label !== id) current.label = label;
        return current;
      }
      const next = { id, label, registeredCount: 0 };
      categories.set(id, next);
      return next;
    };

    snapshot?.models.forEach((model) => {
      const id = providerId(model.provider);
      ensureCategory(id, providerLabel(model.provider_display, model.provider)).registeredCount += 1;
    });
    return [...categories.values()].sort((left, right) => (
      left.label.localeCompare(right.label, "zh-CN") || left.id.localeCompare(right.id)
    ));
  }, [snapshot]);

  useEffect(() => {
    if (
      selectedProvider !== ALL_PROVIDERS_VALUE
      && !providerCategories.some((category) => category.id === selectedProvider)
    ) {
      setSelectedProvider(ALL_PROVIDERS_VALUE);
    }
  }, [providerCategories, selectedProvider]);

  const selectedProviderCategory = providerCategories.find((category) => category.id === selectedProvider);
  const hasSearchQuery = searchQuery.trim().length > 0;
  const visibleModels = useMemo(() => (
    (snapshot?.models ?? []).filter((model) => (
      (selectedProvider === ALL_PROVIDERS_VALUE || providerId(model.provider) === selectedProvider)
      && matchesModelSearch(searchQuery, [
        model.name,
        model.model_id,
        model.provider,
        model.provider_display,
      ])
    ))
  ), [searchQuery, selectedProvider, snapshot?.models]);
  const managementBusy = Boolean(pendingAction);

  const stats = useMemo(() => ({
    registered: snapshot?.models.length ?? 0,
    selectable: snapshot?.models.filter(registeredModelIsSelectable).length ?? 0,
  }), [snapshot]);

  const closeActionDialog = useCallback(() => {
    if (managementBusy) return;
    setAction(null);
    setReason("");
  }, [managementBusy]);

  const refreshAfterVisibility = useCallback(async () => {
    const snapshotPromise = loadSnapshot(false, true);
    const catalog = await refreshModels();
    dispatch(updateProviders(catalog.providers));
    dispatch(updateModels(catalog.models));
    const nextSnapshot = await snapshotPromise;
    if (!nextSnapshot) {
      throw new Error("管理快照刷新失败");
    }
  }, [dispatch, loadSnapshot]);

  const refreshManagementData = useCallback(async () => {
    if (managementBusy) return;
    setPendingAction("refresh");
    setError(null);
    setNotice(null);
    try {
      await refreshAfterVisibility();
      // 保留本轮刷新期间终态同步写入的具体结果，避免较晚完成的通用提示覆盖它。
      setNotice((current) => current ?? "模型管理数据和模型选择器已刷新");
    } catch (caught: unknown) {
      if (isAdminAccessError(caught)) {
        denyAccess();
        return;
      }
      setError(`刷新失败：${errorMessage(caught, "请稍后重试")}`);
    } finally {
      setPendingAction(null);
    }
  }, [denyAccess, managementBusy, refreshAfterVisibility]);

  const submitAction = useCallback(async () => {
    if (!action || !reason.trim() || managementBusy) return;
    const normalizedReason = reason.trim();
    setPendingAction(`visibility:${action.model.model_id}`);
    setError(null);
    setNotice(null);
    let visibilityUpdated = false;

    try {
      await updateModelVisibilityAPI(action.model.model_id, {
        selectable: action.nextSelectable,
        reason: normalizedReason,
        expected_revision: action.model.revision,
      });
      visibilityUpdated = true;
      await refreshAfterVisibility();
      setNotice(action.nextSelectable
        ? registeredModelIsUnhealthy(action.model)
          ? `${action.model.name} 已恢复显示，健康恢复后才可用于新对话`
          : `${action.model.name} 已恢复到新对话模型选择器`
        : `${action.model.name} 已从新选择中隐藏，已有对话仍可用`);
      setAction(null);
      setReason("");
    } catch (caught: unknown) {
      if (isAdminAccessError(caught)) {
        denyAccess();
        return;
      }
      if (visibilityUpdated) {
        setAction(null);
        setReason("");
        setNotice(`${action.model.name} 的可见性已更新，请手动刷新确认最新状态`);
        setError(`可见性已更新，但后续页面或模型目录刷新未完成：${errorMessage(caught, "请手动刷新")}`);
      } else {
        setError(errorMessage(caught, "模型可见性更新失败"));
      }
    } finally {
      setPendingAction(null);
    }
  }, [action, denyAccess, managementBusy, reason, refreshAfterVisibility]);

  if (loading) {
    return (
      <Card className="border-muted shadow-sm">
        <CardContent className="flex h-32 items-center justify-center text-sm text-muted-foreground" role="status">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          正在加载模型管理数据
        </CardContent>
      </Card>
    );
  }

  if (accessDenied) {
    return (
      <Card className="border-muted shadow-sm">
        <CardContent className="flex h-32 items-center justify-center gap-2 text-sm text-muted-foreground" role="status">
          <AlertTriangle className="h-4 w-4" />
          当前账号无权访问模型管理
        </CardContent>
      </Card>
    );
  }

  if (!snapshot) {
    return (
      <Card className="border-muted shadow-sm">
        <CardContent className="flex items-center justify-between gap-4 p-4">
          <div className="flex items-center gap-2 text-sm text-destructive" role="alert">
            <AlertTriangle className="h-4 w-4" />
            {error || "模型管理数据加载失败"}
          </div>
          <Button size="sm" variant="outline" onClick={() => void loadSnapshot(true)}>
            <RefreshCw className="h-4 w-4" />
            重试
          </Button>
        </CardContent>
      </Card>
    );
  }

  const actionTitle = action
    ? (action.nextSelectable
        ? registeredModelIsUnhealthy(action.model)
          ? `确认恢复显示 ${action.model.name}`
          : `确认恢复 ${action.model.name}`
        : `确认隐藏 ${action.model.name}`)
    : "确认模型管理操作";
  const confirmLabel = action?.nextSelectable ? "确认恢复" : "确认隐藏";

  return (
    <div ref={panelRef} tabIndex={-1} className="space-y-4">
      <Card className="border-muted shadow-sm">
        <CardHeader className="border-b bg-muted/10 pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <Bot className="h-5 w-5 text-primary" />
                模型管理
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                控制新对话可选择的模型。模型上下线通过运维命令 model_onboard 完成。
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={managementBusy}
              onClick={() => void refreshManagementData()}
            >
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">已注册模型</p>
              <p data-testid="registered-model-count" className="mt-1 text-2xl font-semibold">{stats.registered}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">新对话可选择</p>
              <p data-testid="selectable-model-count" className="mt-1 text-2xl font-semibold">{stats.selectable}</p>
            </div>
          </div>
          <div className="grid gap-3 border-t pt-4 lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-end">
            <div className="space-y-2">
              <label htmlFor="model-management-search" className="text-sm font-medium">搜索模型</label>
              <div className="relative">
                <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="model-management-search"
                  type="text"
                  role="searchbox"
                  aria-label="搜索模型"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="搜索模型名称、ID 或提供商"
                  className="pl-9 pr-9"
                />
                {hasSearchQuery && (
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    aria-label="清除模型搜索"
                    className="absolute right-1 top-1/2 size-7 -translate-y-1/2 text-muted-foreground"
                    onClick={() => setSearchQuery("")}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">提供商分类</p>
                <span className="text-xs text-muted-foreground">{providerCategories.length} 个</span>
              </div>
              <Select value={selectedProvider} onValueChange={setSelectedProvider}>
                <SelectTrigger aria-label="按提供商筛选模型" className="w-full">
                  {selectedProviderCategory ? (
                    <span className="flex min-w-0 items-center gap-2">
                      <ProviderIcon providerId={selectedProviderCategory.id} size={18} />
                      <span className="truncate">{selectedProviderCategory.label}</span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {selectedProviderCategory.registeredCount}
                      </span>
                    </span>
                  ) : (
                    <span>全部提供商（{stats.registered}）</span>
                  )}
                </SelectTrigger>
                <SelectContent className="w-[max(304px,var(--radix-select-trigger-width))] max-w-[calc(100vw-2rem)]">
                  <SelectItem value={ALL_PROVIDERS_VALUE} textValue="全部提供商" className="min-h-10 py-2">
                    <span className="flex min-w-0 items-center gap-3">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] font-semibold">全</span>
                      <span className="min-w-0 flex-1">全部提供商</span>
                      <span className="text-xs text-muted-foreground">
                        {stats.registered} 已注册
                      </span>
                    </span>
                  </SelectItem>
                  {providerCategories.map((category) => (
                    <SelectItem
                      key={category.id}
                      value={category.id}
                      textValue={category.label}
                      className="min-h-10 py-2"
                    >
                      <span className="flex min-w-0 items-center gap-3">
                        <ProviderIcon providerId={category.id} size={20} />
                        <span className="min-w-0 flex-1 truncate" title={category.label}>{category.label}</span>
                        <span className="whitespace-nowrap text-xs text-muted-foreground">
                          {category.registeredCount} 已注册
                        </span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-primary/30 bg-primary/5 p-3 text-sm" role="status">
          {notice}
        </div>
      )}

      <Card className="border-muted shadow-sm">
        <CardHeader className="border-b bg-muted/10 pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            已注册模型
            <Badge variant="outline" data-testid="visible-registered-model-count">
              {visibleModels.length} / {snapshot.models.length}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 pt-4">
          {snapshot.models.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">暂无已注册模型</p>
          )}
          {snapshot.models.length > 0 && visibleModels.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {hasSearchQuery
                ? (selectedProvider === ALL_PROVIDERS_VALUE
                    ? "没有匹配的已注册模型"
                    : "当前提供商没有匹配的已注册模型")
                : "当前提供商没有已注册模型"}
            </p>
          )}
          {visibleModels.map((model) => (
            <div key={model.model_id} className="rounded-md border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{model.name}</p>
                    <Badge tone={!model.selectable ? "neutral" : !model.routable ? "danger" : ["active", "selectable"].includes(model.state) ? "info" : "neutral"}>{registeredStateLabel(model)}</Badge>
                    <Badge tone={modelHealthStatus(model.health) === "healthy" ? "success" : modelHealthStatus(model.health) === "unhealthy" ? "danger" : "neutral"}>{healthLabel(model.health)}</Badge>
                  </div>
                  <p className="mt-1 break-all text-xs text-muted-foreground">{model.provider_display} · {model.model_id}</p>
                  {!model.selectable && (
                    <p className="mt-2 text-sm text-muted-foreground">
                      {model.routable
                        ? registeredModelIsUnhealthy(model)
                          ? "当前健康异常；可恢复显示，健康恢复后才可用于新对话。"
                          : "仅从新选择中隐藏，已有对话仍可用。"
                        : "当前模型不可路由，无法恢复到新对话选择器。"}
                    </p>
                  )}
                  {model.reason && <p className="mt-1 text-xs text-muted-foreground">最近原因：{model.reason}</p>}
                </div>
                <Button
                  size="sm"
                  variant={model.selectable ? "outline" : "default"}
                  disabled={managementBusy || (!model.selectable && !model.routable)}
                  aria-label={`${model.selectable ? "隐藏" : model.routable ? registeredModelIsUnhealthy(model) ? "恢复显示" : "恢复" : "不可恢复"} ${model.name}`}
                  onClick={(event) => {
                    actionFocus.captureOpener(event.currentTarget);
                    if (!model.selectable && !model.routable) return;
                    setAction({ model, nextSelectable: !model.selectable });
                    setReason("");
                  }}
                >
                  {model.selectable ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  {model.selectable ? "隐藏" : model.routable ? registeredModelIsUnhealthy(model) ? "恢复显示" : "恢复" : "不可恢复"}
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Dialog open={Boolean(action)} onOpenChange={(open) => !open && closeActionDialog()}>
        <DialogContent className="sm:max-w-lg" onOpenAutoFocus={actionFocus.onOpenAutoFocus} onCloseAutoFocus={actionFocus.onCloseAutoFocus}>
          <DialogHeader>
            <DialogTitle>{actionTitle}</DialogTitle>
            <DialogDescription>
              {action && !action.nextSelectable
                ? "仅从新选择中隐藏，已有对话仍可用。请填写原因后确认。"
                : action && registeredModelIsUnhealthy(action.model)
                  ? "仅恢复模型选择器中的可见性；当前健康异常，健康恢复后才可用于新对话。请填写原因后确认。"
                  : "恢复后，新对话可以再次选择这个模型。请填写原因后确认。"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <label htmlFor="model-management-reason" className="text-sm font-medium">操作原因</label>
            <Input
              id="model-management-reason"
              value={reason}
              maxLength={300}
              disabled={managementBusy}
              placeholder="说明本次变更依据"
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
              {error}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" disabled={managementBusy} onClick={closeActionDialog}>取消</Button>
            <Button
              variant={action && !action.nextSelectable ? "destructive" : "default"}
              disabled={!reason.trim() || managementBusy}
              onClick={() => void submitAction()}
            >
              {managementBusy ? "处理中" : confirmLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
