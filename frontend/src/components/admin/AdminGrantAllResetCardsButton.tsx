'use client';

import { useState } from 'react';
import { Ticket } from 'lucide-react';

import { Button } from '@/components/ui/button';
import ConfirmDialog from '@/components/ui/confirm-dialog';
import { grantResetCardsToAll } from '@/lib/api/credits';

/** 全员发放重置卡：给发放时已有的每个受限登录账号各发一张，30 天内有效；游客、管理员和免限额账号不发。 */
export default function AdminGrantAllResetCardsButton() {
  const [confirming, setConfirming] = useState(false);
  const [granting, setGranting] = useState(false);
  const [granted, setGranted] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const grant = async () => {
    setGranting(true);
    setError(null);
    try {
      setGranted((await grantResetCardsToAll()).granted);
    } catch {
      setError('全员发放失败，请稍后重试');
    } finally {
      setGranting(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {error ? (
        <span className="text-xs text-destructive">{error}</span>
      ) : granted !== null ? (
        <span className="text-xs text-muted-foreground" data-testid="reset-cards-granted">已发放 {granted} 张</span>
      ) : null}
      <Button variant="outline" size="sm" onClick={() => setConfirming(true)} disabled={granting}>
        <Ticket />{granting ? '正在发放…' : '全员发放重置卡'}
      </Button>
      <ConfirmDialog
        isOpen={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void grant()}
        title="全员发放重置卡"
        description="给现有的每个普通登录账号各发一张重置卡，30 天内有效，发出后不能收回。游客、管理员和免限额账号不发，之后注册的账号也不会补发。"
        confirmLabel="确认发放"
        cancelLabel="取消"
      />
    </div>
  );
}
