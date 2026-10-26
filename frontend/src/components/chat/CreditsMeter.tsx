'use client';

import { useEffect, useState } from 'react';
import { Infinity as InfinityIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  fetchConversationCredits,
  formatCredits,
  spendResetCard,
  type ConversationCredits,
  type CreditsSummary,
  type CreditsWindow,
  type ResetCards,
} from '@/lib/api/credits';
import { ApiError } from '@/types/api';
import { cn } from '@/lib/utils';

const RING_RADIUS = 6;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;
// 用到八成开始提醒
const WARN_RATIO = 0.8;

function usedRatio(window: CreditsWindow): number {
  if (!window.limit) return 0;
  return Math.min(1, Math.max(0, window.used / window.limit));
}

function levelColor(ratio: number): string {
  if (ratio >= 1) return 'var(--danger)';
  if (ratio >= WARN_RATIO) return 'var(--warn)';
  return 'var(--info)';
}

function WindowUsage({ label, window, resetHint }: { label: string; window: CreditsWindow; resetHint: string }) {
  const ratio = usedRatio(window);
  const remaining = Math.max(0, window.remaining ?? 0);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-foreground">{label}</span>
        <span className="tabular-nums text-muted-foreground">
          <span className="text-foreground">{formatCredits(window.used)}</span>
          {' / '}{formatCredits(window.limit ?? 0)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{ width: `${ratio * 100}%`, background: levelColor(ratio) }}
        />
      </div>
      <div className="flex justify-between gap-3 text-muted-foreground">
        <span className="tabular-nums">{remaining > 0 ? `剩余 ${formatCredits(remaining)}` : '已用完'}</span>
        <span>{resetHint}</span>
      </div>
    </div>
  );
}

function UnlimitedUsage({ label, window }: { label: string; window: CreditsWindow }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="font-medium text-foreground">{label}已用</span>
      <span className="tabular-nums text-foreground">{formatCredits(window.used)}</span>
    </div>
  );
}

type ConversationLoad =
  | { status: 'loading' }
  | { status: 'ready'; data: ConversationCredits }
  | { status: 'error' };

function ConversationUsage({ load }: { load: ConversationLoad }) {
  const value =
    load.status === 'ready' ? formatCredits(load.data.credits) : load.status === 'error' ? '暂不可用' : '计算中';
  const pending = load.status === 'ready' ? load.data.pending_runs : 0;
  return (
    <div className="space-y-1 border-t pt-2.5" data-testid="credits-meter-conversation">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-foreground">本对话已用</span>
        <span className={cn('tabular-nums', load.status === 'ready' ? 'text-foreground' : 'text-muted-foreground')}>
          {value}
        </span>
      </div>
      {pending > 0 && <div className="text-muted-foreground">还有 {pending} 次生成正在结算，暂未计入</div>}
    </div>
  );
}

function formatExpiry(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', timeZone: 'Asia/Shanghai' });
}

/** 有重置卡时显示张数与最早到期日；使用要二次确认，成功后由上层刷新额度。 */
function ResetCardSection({ cards, onUsed }: { cards: ResetCards; onUsed?: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [using, setUsing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const use = async () => {
    setUsing(true);
    setError(null);
    try {
      await spendResetCard();
      setConfirming(false);
      onUsed?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '使用失败，请稍后重试');
    } finally {
      setUsing(false);
    }
  };

  return (
    <div className="space-y-2 border-t pt-2.5" data-testid="credits-meter-reset-cards">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-foreground">重置卡</span>
        <span className="tabular-nums text-foreground">{cards.available} 张</span>
      </div>
      {cards.next_expires_at && (
        <div className="text-muted-foreground">最早一张 {formatExpiry(cards.next_expires_at)}到期</div>
      )}
      {confirming ? (
        <div className="space-y-2">
          <p className="leading-relaxed text-muted-foreground">清零今日和本周的已用量。周一 0 点照常重新计算，越早用越划算。</p>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" className="h-7 flex-1 text-xs" disabled={using} onClick={() => setConfirming(false)}>
              取消
            </Button>
            <Button type="button" size="sm" className="h-7 flex-1 text-xs" disabled={using} onClick={use}>
              {using ? '正在重置…' : '确认使用'}
            </Button>
          </div>
        </div>
      ) : (
        <Button type="button" variant="outline" size="sm" className="h-7 w-full text-xs" onClick={() => setConfirming(true)}>
          使用一张重置卡
        </Button>
      )}
      {error && <p className="text-destructive">{error}</p>}
    </div>
  );
}

/** 输入框工具栏的额度环：环按日、周里用得更满的窗口画，点开看两个窗口的总额度与已用量。
 * 不限额账号（管理员、评测白名单）显示无穷符号，只看已用量。打开着对话时，点开才拉这个对话的累计消耗；
 * 有重置卡时可在这里使用。 */
export default function CreditsMeter({
  credits,
  isGuest,
  onLogin,
  conversationId = null,
  onCreditsChanged,
}: {
  credits: CreditsSummary;
  isGuest: boolean;
  onLogin: () => void;
  conversationId?: string | null;
  /** 用了重置卡后通知上层重新拉额度 */
  onCreditsChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [conversationLoad, setConversationLoad] = useState<ConversationLoad>({ status: 'loading' });

  useEffect(() => {
    if (!open || !conversationId) return;
    const controller = new AbortController();
    setConversationLoad({ status: 'loading' });
    fetchConversationCredits(conversationId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setConversationLoad({ status: 'ready', data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setConversationLoad({ status: 'error' });
      });
    return () => controller.abort();
  }, [conversationId, open]);

  const ratio = Math.max(usedRatio(credits.daily), usedRatio(credits.weekly));
  const percent = Math.round(ratio * 100);
  const label = credits.unlimited ? '额度不限' : `额度已用 ${percent}%`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={label}
          title={label}
          data-testid="credits-meter"
          className="h-8 shrink-0 gap-1.5 px-2 has-[>svg]:px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
        >
          {credits.unlimited ? (
            <InfinityIcon className="h-4 w-4" aria-hidden="true" />
          ) : (
            <svg viewBox="0 0 16 16" className="h-4 w-4 -rotate-90" aria-hidden="true">
              <circle cx="8" cy="8" r={RING_RADIUS} fill="none" stroke="var(--border)" strokeWidth="2.5" />
              <circle
                cx="8"
                cy="8"
                r={RING_RADIUS}
                fill="none"
                stroke={levelColor(ratio)}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeDasharray={RING_CIRCUMFERENCE}
                strokeDashoffset={RING_CIRCUMFERENCE * (1 - ratio)}
                className={cn('transition-[stroke-dashoffset] duration-300', ratio === 0 && 'opacity-0')}
              />
            </svg>
          )}
          {!credits.unlimited && <span className="tabular-nums">{percent}%</span>}
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-64 space-y-3 rounded-xl p-3.5 text-xs" data-testid="credits-meter-panel">
        <div className="flex items-baseline justify-between">
          <span className="text-sm font-semibold">额度</span>
          <span className="text-muted-foreground">
            {credits.unlimited ? '不限额' : isGuest ? '免登录' : '单位 credits'}
          </span>
        </div>
        {credits.unlimited ? (
          <div className="space-y-1.5">
            <UnlimitedUsage label="今日" window={credits.daily} />
            <UnlimitedUsage label="本周" window={credits.weekly} />
          </div>
        ) : (
          <>
            <WindowUsage label="今日" window={credits.daily} resetHint="明天 0 点恢复" />
            <WindowUsage label="本周" window={credits.weekly} resetHint="下周一 0 点恢复" />
          </>
        )}
        {conversationId && <ConversationUsage load={conversationLoad} />}
        {!credits.unlimited && credits.reset_cards.available > 0 && (
          <ResetCardSection cards={credits.reset_cards} onUsed={onCreditsChanged} />
        )}
        <p className="border-t pt-2.5 leading-relaxed text-muted-foreground">
          按模型与工具的实际用量扣除，每条回答的消耗可在回答下方查看。
        </p>
        {isGuest && (
          <Button type="button" size="sm" className="h-7 w-full text-xs" onClick={onLogin}>
            登录获取更多额度
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}
