"use client";

import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { SettingsButton } from "@/components/settings/SettingsControls";
import { AdminPanelHeader } from "@/components/admin/AdminPanelPrimitives";
import EmailUsageMonitor from "./EmailUsageMonitor";
import SearchUsageMonitor from "./SearchUsageMonitor";
import {
  createServiceUsageRefreshRegistry,
  ServiceUsageRefreshProvider,
} from "./serviceUsageRefresh";

export default function ServiceUsagePanel() {
  const registry = useMemo(() => createServiceUsageRefreshRegistry(), []);
  const [refreshing, setRefreshing] = useState(false);

  const refreshAll = async () => {
    setRefreshing(true);
    try {
      await registry.refreshAll();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <ServiceUsageRefreshProvider registry={registry}>
      <div data-testid="service-usage-panel">
        <AdminPanelHeader
          title="服务用量"
          description="查看搜索与邮件服务的额度和近期调用量。"
          action={
            <SettingsButton
              aria-label="刷新全部服务用量"
              size="sm"
              variant="outline"
              onClick={() => void refreshAll()}
              disabled={refreshing}
            >
              <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
              刷新全部
            </SettingsButton>
          }
        />
        <div
          data-testid="service-usage-grid"
          className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-2"
        >
          <SearchUsageMonitor />
          <EmailUsageMonitor />
        </div>
      </div>
    </ServiceUsageRefreshProvider>
  );
}
