'use client';

import { useCallback, useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  fetchAdminUserCredits,
  formatCredits,
  resetAdminUserCredits,
  type AdminUserCredits,
  type CreditsEntry,
  type CreditsTier,
  type CreditsWindow,
} from '@/lib/api/credits';
import type { AdminUserSummary } from '@/types/adminAudit';
import { formatAdminDate } from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

const TIER_LABELS: Record<CreditsTier, string> = {
  guest: '游客',
  user: '普通用户',
  admin: '管理员，不限额',
  exempt: '免限额账号',
};

type LoadState = { status: 'loading' } | { status: 'ready'; data: AdminUserCredits } | { status: 'error' };

function WindowRow({ label, window }: { label: string; window: CreditsWindow }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums">
        {formatCredits(window.used)}
        {window.limit !== null ? ` / ${formatCredits(window.limit)}` : ''}
        <span className="ml-2 text-xs text-muted-foreground">{formatAdminDate(window.resets_at)} 重置</span>
      </span>
    </div>
  );
}

function entryDescription(entry: CreditsEntry): string {
  if (entry.kind === 'reset') return '管理员重置额度';
  const status = entry.charged ? '' : '（出错结束，未扣除）';
  return `${entry.model_id ?? '未知模型'}${status}`;
}

/** 管理员查看用户的日、周额度与最近消耗，并可把当前窗口的已用量清零（流水保留）。 */
export default function AdminUserCreditsDialog({
  user,
  onClose,
}: {
  user: Pick<AdminUserSummary, 'id' | 'username' | 'nickname'>;
  onClose: () => void;
}) {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [confirming, setConfirming] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setState({ status: 'loading' });
    fetchAdminUserCredits(user.id, signal)
      .then((data) => {
        if (!signal?.aborted) setState({ status: 'ready', data });
      })
      .catch(() => {
        if (!signal?.aborted) setState({ status: 'error' });
      });
  }, [user.id]);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const reset = async () => {
    setResetting(true);
    setResetError(null);
    try {
      await resetAdminUserCredits(user.id);
      setConfirming(false);
      load();
    } catch {
      setResetError('重置失败，请稍后重试');
    } finally {
      setResetting(false);
    }
  };

  const summary = state.status === 'ready' ? state.data.summary : null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[560px]" data-testid="admin-user-credits">
        <DialogHeader>
          <DialogTitle>{user.nickname || user.username} 的额度</DialogTitle>
          <DialogDescription>
            日额度按北京时间自然日，周额度从周一零点算。重置只清零当前窗口的已用量，消耗记录保留。
          </DialogDescription>
        </DialogHeader>

        {state.status === 'loading' ? (
          <p className="text-sm text-muted-foreground">正在加载…</p>
        ) : state.status === 'error' ? (
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">额度加载失败</span>
            <Button variant="outline" size="sm" onClick={() => load()}>重新加载</Button>
          </div>
        ) : (
          <div className="space-y-4 text-sm">
            <div className="space-y-1.5">
              <div className="flex justify-between gap-3">
                <span className="text-muted-foreground">账号类型</span>
                <span>{TIER_LABELS[state.data.summary.tier]}</span>
              </div>
              <WindowRow label="今日已用" window={state.data.summary.daily} />
              <WindowRow label="本周已用" window={state.data.summary.weekly} />
              {state.data.summary.last_reset_at && (
                <div className="flex justify-between gap-3">
                  <span className="text-muted-foreground">上次重置</span>
                  <span>{formatAdminDate(state.data.summary.last_reset_at)}</span>
                </div>
              )}
            </div>

            <div>
              <div className="mb-1.5 text-xs text-muted-foreground">最近记录</div>
              {state.data.items.length === 0 ? (
                <p className="text-xs text-muted-foreground">暂无记录</p>
              ) : (
                <div className={styles.tableFrame}>
                  <table className={styles.table}>
                    <thead>
                      <tr><th scope="col">时间</th><th scope="col">内容</th><th scope="col" className={styles.numeric}>credits</th></tr>
                    </thead>
                    <tbody>
                      {state.data.items.map((entry) => (
                        <tr key={entry.id}>
                          <td className="text-xs text-muted-foreground">{formatAdminDate(entry.occurred_at)}</td>
                          <td>{entryDescription(entry)}</td>
                          <td className={styles.numeric}>{entry.kind === 'reset' ? '—' : formatCredits(entry.credits)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {summary && !summary.unlimited && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-3">
            {resetError && <span className="mr-auto text-xs text-destructive">{resetError}</span>}
            {confirming ? (
              <>
                <span className="text-xs text-muted-foreground">清零今日和本周的已用量？</span>
                <Button variant="outline" size="sm" onClick={() => setConfirming(false)} disabled={resetting}>取消</Button>
                <Button variant="destructive" size="sm" onClick={reset} disabled={resetting}>
                  {resetting ? '正在重置…' : '确认重置'}
                </Button>
              </>
            ) : (
              <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
                <RotateCcw />重置额度
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
