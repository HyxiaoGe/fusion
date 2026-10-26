'use client';

import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import ConfirmDialog from '@/components/ui/confirm-dialog';
import { fetchCreditsResetAll, resetAllCredits } from '@/lib/api/credits';
import { formatAdminDate } from './AdminPanelPrimitives';

/** 全员重置额度：所有账号当前日、周窗口的已用量清零，消耗记录保留，窗口照常按自然日和周一计算。 */
export default function AdminResetAllCreditsButton() {
  const [lastResetAt, setLastResetAt] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchCreditsResetAll(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setLastResetAt(data.last_reset_at);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  const reset = async () => {
    setResetting(true);
    setError(null);
    try {
      setLastResetAt((await resetAllCredits()).last_reset_at);
    } catch {
      setError('全员重置失败，请稍后重试');
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {error ? (
        <span className="text-xs text-destructive">{error}</span>
      ) : lastResetAt ? (
        <span className="text-xs text-muted-foreground" data-testid="credits-reset-all-at">
          上次全员重置 {formatAdminDate(lastResetAt)}
        </span>
      ) : null}
      <Button variant="outline" size="sm" onClick={() => setConfirming(true)} disabled={resetting}>
        <RotateCcw />{resetting ? '正在重置…' : '全员重置额度'}
      </Button>
      <ConfirmDialog
        isOpen={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void reset()}
        title="全员重置额度"
        description="所有账号今日和本周的已用量清零，消耗记录保留。日、周额度仍按自然日和周一 0 点计算，不会顺延。"
        confirmLabel="确认重置"
        cancelLabel="取消"
        variant="destructive"
      />
    </div>
  );
}
