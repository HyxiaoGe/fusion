'use client';

import { Check, Copy, Edit2, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import styles from './MessageActions.module.css';

interface MessageActionsProps {
  timestamp?: number;
  copied?: boolean;
  onCopy?: () => void;
  onRetry?: () => void;
  onEdit?: () => void;
  retryLabel: string;
  className?: string;
}

function formatTime(timestamp?: number) {
  if (!timestamp || isNaN(timestamp)) return '';
  try {
    const date = new Date(Number(timestamp));
    if (isNaN(date.getTime())) return '';
    return date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  } catch {
    return '';
  }
}

function MessageActions({
  timestamp,
  copied = false,
  onCopy,
  onRetry,
  onEdit,
  retryLabel,
  className,
}: MessageActionsProps) {
  const formattedTime = formatTime(timestamp);
  const copyLabel = copied ? '已复制' : '复制';

  return (
    <div className={cn(styles.actions, className)}>
      {formattedTime ? (
        <span className="text-xs text-muted-foreground/70 mr-1">
          {formattedTime}
        </span>
      ) : null}
      {onCopy ? (
        <Button
          aria-label={copyLabel}
          title={copyLabel}
          variant="ghost"
          size="icon"
          className={styles.action}
          onClick={onCopy}
        >
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
      ) : null}
      {onEdit ? (
        <Button
          aria-label="编辑"
          title="编辑"
          variant="ghost"
          size="icon"
          className={styles.action}
          onClick={onEdit}
        >
          <Edit2 className="h-4 w-4" />
        </Button>
      ) : null}
      {onRetry ? (
        <Button
          aria-label={retryLabel}
          title={retryLabel}
          variant="ghost"
          size="icon"
          className={styles.action}
          onClick={onRetry}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      ) : null}
    </div>
  );
}

export default MessageActions;
