'use client';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { formatCredits, type CreditsSummary, type CreditsWindow } from '@/lib/api/credits';
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

/** 输入框下方的额度环：环按日、周里用得更满的窗口画，点开看两个窗口的总额度与已用量。 */
export default function CreditsMeter({
  credits,
  isGuest,
  onLogin,
}: {
  credits: CreditsSummary;
  isGuest: boolean;
  onLogin: () => void;
}) {
  const ratio = Math.max(usedRatio(credits.daily), usedRatio(credits.weekly));
  const percent = Math.round(ratio * 100);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={`额度已用 ${percent}%`}
          title={`额度已用 ${percent}%`}
          data-testid="credits-meter"
          className="-my-0.5 h-5 gap-1 rounded-full px-1.5 has-[>svg]:px-1.5 text-xs font-normal text-muted-foreground hover:text-foreground"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 -rotate-90" aria-hidden="true">
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
          <span className="tabular-nums">{percent}%</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" className="w-64 space-y-3 rounded-xl p-3.5 text-xs" data-testid="credits-meter-panel">
        <div className="flex items-baseline justify-between">
          <span className="text-sm font-semibold">额度</span>
          <span className="text-muted-foreground">{isGuest ? '免登录' : '单位 credits'}</span>
        </div>
        <WindowUsage label="今日" window={credits.daily} resetHint="明天 0 点恢复" />
        <WindowUsage label="本周" window={credits.weekly} resetHint="下周一 0 点恢复" />
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
