import type { ReactNode } from "react";
import { AlertCircle, Inbox, Loader2, type LucideIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import styles from "./SettingsMonitoring.module.css";

// 三个只读监控面板共用状态布局，始终保留所属服务，避免失败提示失去上下文。
export function SettingsMonitoringState({ title, icon: Icon, state, children, action }: {
  title: string;
  icon: LucideIcon;
  state: "loading" | "error" | "empty";
  children: ReactNode;
  action?: ReactNode;
}) {
  const StateIcon = state === "loading" ? Loader2 : state === "error" ? AlertCircle : Inbox;
  return (
    <Card className={`h-full border-border ${styles.card}`}>
      <CardHeader className={styles.header}>
        <CardTitle className={styles.title}>
          <span className={styles.icon}><Icon className="h-4 w-4" aria-hidden="true" /></span>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className={styles.body}>
        <div className={styles.state} data-state={state} role={state === "error" ? "alert" : state === "loading" ? "status" : undefined}>
          <StateIcon className={`${styles.stateIcon} ${state === "loading" ? "animate-spin" : ""}`} aria-hidden="true" />
          <div>{children}</div>
          {action}
        </div>
      </CardContent>
    </Card>
  );
}
