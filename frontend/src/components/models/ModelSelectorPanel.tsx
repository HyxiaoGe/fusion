import { memo, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Check, AlertCircle } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  buildModelCapabilityLabels,
  buildModelCapabilityRecommendation,
  buildModelCapabilityTooltip,
} from "@/lib/models/modelCapabilityPresentation";
import { CapabilityChipList } from "./CapabilityChip";
import ProviderIcon from "./ProviderIcon";
import type { ModelInfo, ProviderInfo } from "@/lib/config/modelConfig";
import { isModelSelectable } from "@/lib/models/modelPreference";
import styles from "./ModelSelectorGlass.module.css";

// 模型 health.status === 'unhealthy' 时 FE 灰显并禁用点击。
// 'unknown'（后台第一次还没探完）按健康处理，避免冷启动期间全列表灰掉。
const isUnhealthy = (model: ModelInfo) => model.health?.status === "unhealthy";
// 后端已经把 raw error 翻成中文友好句（litellm_health._classify_error），FE 直接展示。
const healthTooltip = (model: ModelInfo) => model.health?.error || "服务商暂时不可用";

/* ---------- types ---------- */

interface ProviderGroup extends ProviderInfo {
  models: ModelInfo[];
}

interface ModelSelectorPanelProps {
  autoModel?: ModelInfo | null;
  modelsByProvider: ProviderGroup[];
  selectedModelId: string | null;
  recentModelIds: string[];
  allModels: ModelInfo[];
  activeProvider: string;
  onSelect: (modelId: string) => void;
  onProviderChange: (providerId: string) => void;
}

/* ---------- RecentModels ---------- */

const RecentModels = memo(
  ({
    modelIds,
    allModels,
    selectedModelId,
    onSelect,
  }: {
    modelIds: string[];
    allModels: ModelInfo[];
    selectedModelId: string | null;
    onSelect: (id: string) => void;
  }) => {
    const recentModels = modelIds
      .map((id) => allModels.find((m) => m.id === id))
      .filter((m): m is ModelInfo => m != null && isModelSelectable(m));

    if (recentModels.length === 0) return null;

    return (
      <div className="px-3 pt-2.5 pb-1.5">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
          最近使用
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {recentModels.map((model) => {
            const unhealthy = isUnhealthy(model);
            const btn = (
              <button
                key={model.id}
                onClick={() => !unhealthy && onSelect(model.id)}
                disabled={unhealthy}
                aria-pressed={model.id === selectedModelId}
                className={cn(
                  "inline-flex items-center gap-1.5 pl-1 pr-2.5 py-1 text-[11px]",
                  styles.recentTile,
                  unhealthy
                    ? "text-muted-foreground/60 cursor-not-allowed opacity-60"
                    : model.id === selectedModelId
                      ? cn(styles.tileSelected, "text-foreground")
                      : "text-muted-foreground hover:text-foreground",
                )}
              >
                <ProviderIcon providerId={model.provider} size={18} className="rounded" />
                {model.name}
                {unhealthy && <AlertCircle size={11} className="text-amber-600 dark:text-amber-500" />}
              </button>
            );
            if (!unhealthy) return btn;
            return (
              <Tooltip key={model.id}>
                <TooltipTrigger asChild>
                  <span className="inline-block">{btn}</span>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs text-xs">
                  {healthTooltip(model)}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>
      </div>
    );
  },
);
RecentModels.displayName = "RecentModels";

/* ---------- ProviderTabs ---------- */

const ProviderTabs = memo(
  ({
    providers,
    activeProvider,
    onProviderChange,
  }: {
    providers: ProviderGroup[];
    activeProvider: string;
    onProviderChange: (id: string) => void;
  }) => {
    const containerRef = useRef<HTMLDivElement>(null);

    const handleClick = useCallback(
      (_e: React.MouseEvent<HTMLButtonElement>, providerId: string, idx: number) => {
        onProviderChange(providerId);

        const container = containerRef.current;
        if (!container) return;

        // 滚动到前一个标签的位置，使点击的标签及后续标签完整可见
        const buttons = container.querySelectorAll("button");
        const targetIdx = Math.max(0, idx - 1);
        const targetBtn = buttons[targetIdx];
        if (targetBtn) {
          container.scrollTo({
            left: (targetBtn as HTMLElement).offsetLeft,
            behavior: "smooth",
          });
        }
      },
      [onProviderChange],
    );

    return (
      <div ref={containerRef} className={cn("flex overflow-x-auto scrollbar-hide", styles.providerStrip)}>
        {providers.map((provider, idx) => (
          <button
            key={provider.id}
            onClick={(e) => handleClick(e, provider.id, idx)}
            aria-pressed={provider.id === activeProvider}
            className={cn(
              "px-3 py-2 text-[11px] whitespace-nowrap shrink-0",
              styles.providerTab,
              idx === providers.length - 1 && "pr-6",
              provider.id === activeProvider
                ? cn(styles.providerTabActive, "text-primary font-semibold")
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {provider.name}
          </button>
        ))}
      </div>
    );
  },
);
ProviderTabs.displayName = "ProviderTabs";

/* ---------- ModelCard ---------- */

const ModelCard = memo(
  ({
    model,
    isSelected,
    onSelect,
  }: {
    model: ModelInfo;
    isSelected: boolean;
    onSelect: () => void;
  }) => {
    const unhealthy = isUnhealthy(model);
    const recommendation = buildModelCapabilityRecommendation(model);
    const tooltip = buildModelCapabilityTooltip(model);
    const card = (
      <button
        onClick={() => !unhealthy && onSelect()}
        disabled={unhealthy}
        aria-pressed={isSelected}
        className={cn(
          "text-left p-2.5 w-full",
          styles.modelTile,
          unhealthy
            ? "opacity-55 cursor-not-allowed"
            : isSelected
              ? styles.tileSelected
              : "",
        )}
      >
        <div className="flex items-center justify-between gap-1">
          <span className={cn("text-sm truncate", isSelected ? "font-semibold" : "font-medium")}>
            {model.name}
          </span>
          <span className="shrink-0 text-[10px] text-muted-foreground">能力 {recommendation.score}</span>
          {isSelected && <Check size={14} className="shrink-0 text-primary" />}
          {unhealthy && <AlertCircle size={13} className="shrink-0 text-amber-600 dark:text-amber-500" />}
        </div>
        <CapabilityChipList labels={buildModelCapabilityLabels(model)} maxCount={5} />
      </button>
    );
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="block">{card}</span>
        </TooltipTrigger>
        <TooltipContent
          side="right"
          align="start"
          sideOffset={8}
          collisionPadding={12}
          className="max-w-xs whitespace-pre-line text-xs"
        >
          {tooltip}
        </TooltipContent>
      </Tooltip>
    );
  },
);
ModelCard.displayName = "ModelCard";

/* ---------- AutoModelCard ---------- */

const AutoModelCard = memo(
  ({
    model,
    isSelected,
    onSelect,
  }: {
    model: ModelInfo;
    isSelected: boolean;
    onSelect: () => void;
  }) => {
    const unhealthy = isUnhealthy(model);
    return (
      <div className="px-2.5 pt-2.5">
        <button
          data-testid="model-selector-auto"
          onClick={() => !unhealthy && onSelect()}
          disabled={unhealthy}
          aria-pressed={isSelected}
          className={cn(
            "flex w-full items-center gap-2.5 p-2.5 text-left",
            styles.modelTile,
            unhealthy
              ? "opacity-55 cursor-not-allowed"
              : isSelected
                ? styles.tileSelected
                : "",
          )}
        >
          <ProviderIcon providerId={model.provider} size={22} className="rounded-md" />
          <span className="flex min-w-0 flex-1 flex-col">
            <span className={cn("text-sm", isSelected ? "font-semibold" : "font-medium")}>
              {model.name}
              <span className="ml-1.5 text-[10px] font-normal text-muted-foreground">推荐</span>
            </span>
            <span className="truncate text-[11px] text-muted-foreground">
              {unhealthy
                ? healthTooltip(model)
                : model.autoResolvedModelName
                  ? `按可用性自动挑选，当前使用 ${model.autoResolvedModelName}`
                  : "按可用性自动挑选模型"}
            </span>
          </span>
          {isSelected && <Check size={14} className="shrink-0 text-primary" />}
        </button>
      </div>
    );
  },
);
AutoModelCard.displayName = "AutoModelCard";

/* ---------- ModelSelectorPanel ---------- */

const ModelSelectorPanel = memo(
  ({
    autoModel = null,
    modelsByProvider,
    selectedModelId,
    recentModelIds,
    allModels,
    activeProvider,
    onSelect,
    onProviderChange,
  }: ModelSelectorPanelProps) => {
    const activeGroup = modelsByProvider.find((g) => g.id === activeProvider);
    const filteredModels = activeGroup?.models || [];

    return (
      <TooltipProvider delayDuration={150}>
        <div>
          {autoModel && (
            <AutoModelCard
              model={autoModel}
              isSelected={autoModel.id === selectedModelId}
              onSelect={() => onSelect(autoModel.id)}
            />
          )}
          <RecentModels
            modelIds={recentModelIds.filter((id) => id !== autoModel?.id)}
            allModels={allModels}
            selectedModelId={selectedModelId}
            onSelect={onSelect}
          />
          <ProviderTabs
            providers={modelsByProvider}
            activeProvider={activeProvider}
            onProviderChange={onProviderChange}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 p-2.5 min-h-[240px] content-start">
            {filteredModels.map((model) => (
              <ModelCard
                key={model.id}
                model={model}
                isSelected={model.id === selectedModelId}
                onSelect={() => onSelect(model.id)}
              />
            ))}
          </div>
        </div>
      </TooltipProvider>
    );
  },
);
ModelSelectorPanel.displayName = "ModelSelectorPanel";

export default ModelSelectorPanel;
