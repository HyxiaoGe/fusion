"use client";

import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SettingsButton } from "@/components/settings/SettingsControls";
import styles from "@/components/settings/SettingsMonitoring.module.css";
import EmailUsageMonitor from "./EmailUsageMonitor";
import SearchUsageMonitor from "./SearchUsageMonitor";
import {
  createServiceUsageRefreshRegistry,
  ServiceUsageRefreshProvider,
} from "./serviceUsageRefresh";

export default function ServiceUsagePanel() {
  const { t } = useTranslation();
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
      <div
        data-testid="service-usage-panel"
        className="mx-auto w-full max-w-6xl space-y-4 pb-6"
      >
        <div className={`sticky top-0 z-10 flex justify-end py-2 ${styles.toolbar}`}>
          <SettingsButton
            aria-label={t("settings.monitoring.refreshAllLabel", { defaultValue: "刷新全部服务用量" })}
            size="sm"
            variant="outline"
            onClick={() => void refreshAll()}
            disabled={refreshing}
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            {t("settings.monitoring.refreshAll", { defaultValue: "刷新全部" })}
          </SettingsButton>
        </div>
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
