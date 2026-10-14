"use client";

import { useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useNewVersionAvailable } from "@/hooks/useNewVersionAvailable";

/** 线上有新版本时在右上角提示刷新；不自动刷新，避免打断正在进行的对话。 */
export function NewVersionNotice() {
  const { t } = useTranslation();
  const latestVersion = useNewVersionAvailable();
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);

  if (!latestVersion || latestVersion === dismissedVersion) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed right-4 top-4 z-50 w-[min(20rem,calc(100vw-2rem))] rounded-xl border bg-background p-4 shadow-lg"
    >
      <div className="flex items-start gap-3">
        <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-sm font-medium">{t("newVersion.title")}</p>
          <p className="text-xs text-muted-foreground">{t("newVersion.description")}</p>
        </div>
        <button
          type="button"
          aria-label={t("newVersion.dismiss")}
          className="rounded-md p-0.5 text-muted-foreground hover:text-foreground"
          onClick={() => setDismissedVersion(latestVersion)}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={() => setDismissedVersion(latestVersion)}>
          {t("newVersion.later")}
        </Button>
        <Button type="button" size="sm" onClick={() => window.location.reload()}>
          {t("newVersion.refresh")}
        </Button>
      </div>
    </div>
  );
}
