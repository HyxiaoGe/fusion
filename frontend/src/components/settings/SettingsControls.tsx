"use client";

import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
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

export function SettingsInput({ className, ...props }: ComponentProps<typeof Input>) {
  return <Input {...props} data-settings-input className={cn(styles.field, className)} />;
}

export function SettingsTextarea({ className, ...props }: ComponentProps<typeof Textarea>) {
  return <Textarea {...props} data-settings-textarea className={cn(styles.field, styles.textarea, className)} />;
}

export function SettingsSelectTrigger({ className, ...props }: ComponentProps<typeof SelectTrigger>) {
  return <SelectTrigger {...props} data-settings-select className={cn(styles.field, styles.selectTrigger, className)} />;
}

// portal 不继承设置正文的类名，菜单材质由自身样式保持一致。
export function SettingsSelectContent({ className, ...props }: ComponentProps<typeof SelectContent>) {
  return <SelectContent {...props} data-settings-select-content className={cn(styles.selectContent, className)} />;
}

export function SettingsSelectItem({ className, ...props }: ComponentProps<typeof SelectItem>) {
  return <SelectItem {...props} className={cn(styles.selectItem, className)} />;
}

export function SettingsCheckbox({ className, ...props }: Omit<ComponentProps<"input">, "type">) {
  return <input {...props} type="checkbox" data-settings-checkbox className={cn(styles.checkbox, className)} />;
}

export function SettingsFieldError({ className, ...props }: ComponentProps<"p">) {
  return <p role="alert" {...props} data-settings-error className={cn(styles.fieldError, className)} />;
}
