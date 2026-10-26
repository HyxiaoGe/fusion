'use client';

import { useEffect, useMemo, useState } from 'react';
import { Coins } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { fetchMessageCredits, formatCredits, type MessageCredits as MessageCreditsData } from '@/lib/api/credits';
import { useAppSelector } from '@/redux/hooks';
import { formatTokens, summarizeMessageCredits } from './messageCreditsModel';
import styles from './MessageActions.module.css';

type LoadState =
  | { status: 'idle' | 'loading' }
  | { status: 'ready'; data: MessageCreditsData }
  | { status: 'error' };

/** 回答下方的「消耗」入口：打开时才拉这条回答的结算明细。 */
export default function MessageCredits({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>({ status: 'idle' });
  const models = useAppSelector((root) => root.models.models);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setState({ status: 'loading' });
    fetchMessageCredits(messageId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ status: 'ready', data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ status: 'error' });
      });
    return () => controller.abort();
  }, [messageId, open]);

  const summary = useMemo(
    () => (state.status === 'ready' ? summarizeMessageCredits(state.data) : null),
    [state],
  );
  const modelName = (id: string) => models.find((model) => model.id === id)?.name ?? id;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button aria-label="查看消耗" title="查看消耗" variant="ghost" size="icon" className={styles.action}>
          <Coins className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-3 text-xs" data-testid="message-credits">
        {state.status === 'error' ? (
          <p className="text-muted-foreground">消耗明细加载失败，请稍后再试</p>
        ) : !summary ? (
          <p className="text-muted-foreground">正在加载…</p>
        ) : summary.runs === 0 ? (
          <p className="text-muted-foreground">
            {summary.pending > 0 ? '正在结算，稍后再看' : '这条回答没有消耗记录'}
          </p>
        ) : (
          <div className="space-y-2.5">
            <div className="flex items-baseline justify-between">
              <span className="text-muted-foreground">本条回答消耗</span>
              <span className="text-sm font-semibold tabular-nums">{formatCredits(summary.credits)} credits</span>
            </div>

            {summary.models.length > 0 && (
              <section className="space-y-1">
                <div className="flex justify-between text-muted-foreground">
                  <span>模型</span>
                  <span className="tabular-nums">{formatCredits(summary.modelCredits)}</span>
                </div>
                {summary.models.map((row) => (
                  <div key={row.model} className="pl-2">
                    <div className="flex justify-between gap-2">
                      <span className="truncate">{modelName(row.model)} · {row.rounds} 轮</span>
                      <span className="shrink-0 tabular-nums">{formatCredits(row.credits)}</span>
                    </div>
                    <div className="text-muted-foreground">
                      输入 {formatTokens(row.inputTokens)}
                      {row.cacheReadTokens > 0 && `（缓存命中 ${formatTokens(row.cacheReadTokens)}）`}
                      {' · '}输出 {formatTokens(row.outputTokens)}
                    </div>
                  </div>
                ))}
              </section>
            )}

            {summary.tools.length > 0 && (
              <section className="space-y-1">
                <div className="flex justify-between text-muted-foreground">
                  <span>工具</span>
                  <span className="tabular-nums">{formatCredits(summary.toolCredits)}</span>
                </div>
                {summary.tools.map((row) => (
                  <div key={row.label} className="flex justify-between gap-2 pl-2">
                    <span className="truncate">{row.label} ×{row.count}</span>
                    <span className="shrink-0 tabular-nums">{formatCredits(row.credits)}</span>
                  </div>
                ))}
              </section>
            )}

            <ul className="space-y-0.5 border-t pt-2 text-muted-foreground">
              {summary.runs > 1 && <li>共 {summary.runs} 次生成（含重新生成、继续生成）</li>}
              {summary.uncharged > 0 && <li>{summary.uncharged} 次生成出错结束，没有扣除</li>}
              {summary.pending > 0 && <li>还有 {summary.pending} 次生成正在结算</li>}
              {summary.offPeak && <li>部分轮次按空闲时段价计算</li>}
              <li>按模型实际用量计价，工具按调用次数计价</li>
            </ul>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
