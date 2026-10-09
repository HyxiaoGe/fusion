"use client";

import { SettingsBadge as Badge, type SettingsStatusTone } from "@/components/settings/SettingsControls";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { McpServer, McpServerModelView } from "@/types/mcp";

type PreviewStatus = "direct" | "on_demand" | "hidden" | "component" | "authorized_unused" | "unauthorized";

interface PreviewRow {
  key: string;
  title: string;
  subtitle?: string;
  description: string;
  status: PreviewStatus;
  note?: string;
}

const statusPresentation: Record<PreviewStatus, { label: string; tone: SettingsStatusTone }> = {
  direct: { label: "直接提供", tone: "success" },
  on_demand: { label: "按需加载", tone: "neutral" },
  hidden: { label: "暂不提供", tone: "warning" },
  component: { label: "供产品工具调用", tone: "info" },
  authorized_unused: { label: "已授权，本次未提供", tone: "neutral" },
  unauthorized: { label: "未授权", tone: "neutral" },
};

function formatResetIn(seconds: number): string {
  if (seconds >= 3600) return `约 ${Math.round(seconds / 3600)} 小时后恢复`;
  return `约 ${Math.max(1, Math.round(seconds / 60))} 分钟后恢复`;
}

export function buildToolPreview(server: McpServer, view: McpServerModelView | undefined): {
  modelTools: PreviewRow[];
  remoteTools: PreviewRow[];
} {
  const descriptions = new Map(server.discovered_tools.map((tool) => [tool.name, tool.description ?? ""]));
  const modelTools: PreviewRow[] = [];
  const shownRemote = new Set<string>();
  const productSources = new Set<string>();
  for (const tool of view?.tools ?? []) {
    if (tool.kind === "product") {
      tool.source_tools.forEach((name) => productSources.add(name));
      modelTools.push({
        key: tool.name,
        title: tool.label,
        subtitle: tool.name,
        description: tool.description ?? "",
        status: tool.mode,
        note: `调用远端工具：${tool.source_tools.join("、")}`,
      });
    } else {
      shownRemote.add(tool.name);
      modelTools.push({
        key: tool.name,
        title: tool.name,
        description: tool.description || descriptions.get(tool.name) || "",
        status: tool.mode,
      });
    }
  }
  for (const tool of view?.hidden_tools ?? []) {
    tool.source_tools?.forEach((name) => productSources.add(name));
    modelTools.push({
      key: tool.name,
      title: tool.label,
      subtitle: tool.name,
      description: tool.description ?? "",
      status: "hidden",
      note: `今日额度已用完，${formatResetIn(tool.resets_in_seconds)}`,
    });
  }
  const allowed = new Set(server.allowed_tools);
  const remoteTools = server.discovered_tools
    .filter((tool) => !shownRemote.has(tool.name))
    .map<PreviewRow>((tool) => ({
      key: tool.name,
      title: tool.name,
      description: tool.description ?? "",
      status: productSources.has(tool.name)
        ? "component"
        : allowed.has(tool.name)
          ? "authorized_unused"
          : "unauthorized",
    }))
    .sort((left, right) => statusOrder(left.status) - statusOrder(right.status));
  return { modelTools, remoteTools };
}

function statusOrder(status: PreviewStatus): number {
  return ["component", "authorized_unused", "unauthorized"].indexOf(status);
}

function PreviewList({ rows }: { rows: PreviewRow[] }) {
  return (
    <ul className="divide-y rounded-md border">
      {rows.map((row) => {
        const status = statusPresentation[row.status];
        return (
          <li key={row.key} className="space-y-1 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{row.title}</span>
              {row.subtitle && <span className="font-mono text-xs text-muted-foreground">{row.subtitle}</span>}
              <Badge tone={status.tone}>{status.label}</Badge>
            </div>
            <p className="text-sm text-muted-foreground">{row.description || "服务没有提供说明"}</p>
            {row.note && <p className="text-xs text-muted-foreground">{row.note}</p>}
          </li>
        );
      })}
    </ul>
  );
}

export default function McpToolPreviewDialog({
  server,
  view,
  open,
  onOpenChange,
}: {
  server: McpServer | null;
  view: McpServerModelView | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const preview = server ? buildToolPreview(server, view) : { modelTools: [], remoteTools: [] };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{server ? `${server.name} · 工具说明` : "工具说明"}</DialogTitle>
          <DialogDescription>说明来自服务方；高德、生图等产品工具为中文简介。</DialogDescription>
        </DialogHeader>
        {server && !server.is_enabled && (
          <p className="text-sm text-muted-foreground">服务已停用，下面的工具都不会提供给模型。</p>
        )}
        {preview.modelTools.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">模型可用工具</h3>
            <PreviewList rows={preview.modelTools} />
          </section>
        )}
        {preview.remoteTools.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-semibold">{preview.modelTools.length > 0 ? "其他远端工具" : "远端工具"}</h3>
            <PreviewList rows={preview.remoteTools} />
          </section>
        )}
      </DialogContent>
    </Dialog>
  );
}
