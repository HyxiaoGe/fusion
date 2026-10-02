"use client";

import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import GlassHoverLens, { pointGlassLight, resetGlassLight } from "@/components/ui/GlassHoverLens";
import { cn } from "@/lib/utils";
import styles from "./SettingsControls.module.css";

export type SettingsStatusTone = "neutral" | "info" | "success" | "warning" | "danger";

// 只在设置模块复用外观，原按钮的事件、禁用、提交和 Radix 触发器协议保持不变。
export function SettingsButton({
  variant = "default", asChild = false, className, children, onPointerMove, onPointerLeave, ...props
}: ComponentProps<typeof Button>) {
  const tone = variant === "default" ? "primary" : variant === "destructive" ? "destructive" : "secondary";
  return (
    <Button
      {...props}
      variant={variant}
      asChild={asChild}
      data-settings-action={tone}
      className={cn("relative", styles.action, styles[tone], variant === "ghost" && styles.quiet, className)}
      onPointerMove={(event) => { pointGlassLight(event); onPointerMove?.(event); }}
      onPointerLeave={(event) => { resetGlassLight(event); onPointerLeave?.(event); }}
    >
      {asChild ? children : <>
        <GlassHoverLens corners={false} />
        <span className={styles.actionContent}>{children}</span>
      </>}
    </Button>
  );
}

export function SettingsBadge({ tone = "neutral", className, ...props }: ComponentProps<typeof Badge> & { tone?: SettingsStatusTone }) {
  return <Badge {...props} data-settings-status={tone} className={cn(styles.badge, styles[tone], className)} />;
}

export function SettingsSwitch({ className, ...props }: ComponentProps<typeof Switch>) {
  return <Switch {...props} data-settings-switch className={cn(styles.switch, className)} />;
}
