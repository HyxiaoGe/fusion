'use client';

import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { AssistantActivity } from './assistantActivity';
import { resolveActivityStatus, type ResolveActivityStatusOptions } from './activityStatusView';
import styles from './MessageAuxiliary.module.css';

interface AssistantActivityStatusProps extends ResolveActivityStatusOptions {
  activity: AssistantActivity;
  className?: string;
}

export default function AssistantActivityStatus({ activity, className, ...options }: AssistantActivityStatusProps) {
  const view = resolveActivityStatus(activity, options);
  if (!view) return null;
  const Icon = view.icon;

  return (
    <div
      key={view.key}
      role={view.role}
      aria-live={view.role === 'alert' ? 'assertive' : 'polite'}
      aria-atomic="true"
      data-tone={view.tone}
      className={cn('flex min-w-0 items-center gap-2', styles.activity, className)}
    >
      <span className={styles.statusIcon} aria-hidden="true">
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span className="max-w-full shrink-0 truncate font-medium">{view.title}</span>
        {view.detail ? <span className="min-w-0 truncate text-muted-foreground">{view.detail}</span> : null}
      </span>
      {view.busy ? (
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
          aria-hidden="true"
        />
      ) : null}
    </div>
  );
}
