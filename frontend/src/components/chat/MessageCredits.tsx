'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Coins } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { fetchMessageCredits, formatCredits, type MessageCredits as MessageCreditsData } from '@/lib/api/credits';
import { useAppSelector } from '@/redux/hooks';
import { formatTokens, summarizeMessageCredits } from './messageCreditsModel';
import styles from './MessageActions.module.css';

const MODEL_COLOR = 'var(--info)';
const TOOL_COLOR = 'var(--teal)';

function CreditsGroup({ label, color, total, children }: {
  label: string;
  color: string;
  total: number;
  children: ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-medium">
          <span className="h-2 w-2 rounded-full" style={{ background: color }} aria-hidden="true" />
          {label}
        </span>
        <span className="font-medium tabular-nums">{formatCredits(total)}</span>
      </div>
      <div className="space-y-1.5 pl-3.5">{children}</div>
    </section>
  );
}

function CreditsItem({ title, credits, detail }: { title: string; credits: number | null; detail?: string }) {
  return (
    <div>
      <div className="flex justify-between gap-2 text-foreground/80">
        <span className="truncate">{title}</span>
        {credits !== null && <span className="shrink-0 tabular-nums">{formatCredits(credits)}</span>}
      </div>
      {detail && <div className="mt-0.5 tabular-nums text-muted-foreground">{detail}</div>}
    </div>
  );
}

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
  const notes = summary
    ? [
        summary.runs > 1 ? `共 ${summary.runs} 次生成（含重新生成、继续生成）` : null,
        summary.uncharged > 0 ? `${summary.uncharged} 次生成出错结束，没有扣除` : null,
        summary.pending > 0 ? `还有 ${summary.pending} 次生成正在结算` : null,
        summary.offPeak ? '部分轮次按空闲时段价计算' : null,
      ].filter((note): note is string => note !== null)
    : [];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button aria-label="查看消耗" title="查看消耗" variant="ghost" size="icon" className={styles.action}>
          <Coins className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 rounded-xl p-0 text-xs" data-testid="message-credits">
        {state.status === 'error' ? (
          <p className="p-3.5 text-muted-foreground">消耗明细加载失败，请稍后再试</p>
        ) : !summary ? (
          <p className="p-3.5 text-muted-foreground">正在加载…</p>
        ) : summary.runs === 0 ? (
          <p className="p-3.5 text-muted-foreground">
            {summary.pending > 0 ? '正在结算，稍后再看' : '这条回答没有消耗记录'}
          </p>
        ) : (
          <>
            <div className="space-y-2.5 p-3.5 pb-3">
              <div className="text-muted-foreground">本条回答消耗</div>
              <div className="flex items-baseline gap-1">
                <span className="text-2xl font-semibold leading-none tabular-nums">{formatCredits(summary.credits)}</span>
                <span className="text-muted-foreground">credits</span>
              </div>
              {summary.credits > 0 && (
                <div className="flex h-1.5 gap-0.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                  {summary.modelCredits > 0 && (
                    <div style={{ flexGrow: summary.modelCredits, background: MODEL_COLOR }} />
                  )}
                  {summary.toolCredits > 0 && (
                    <div style={{ flexGrow: summary.toolCredits, background: TOOL_COLOR }} />
                  )}
                </div>
              )}
            </div>

            <div className="space-y-3 border-t px-3.5 py-3">
              {summary.models.length > 0 && (
                <CreditsGroup label="模型" color={MODEL_COLOR} total={summary.modelCredits}>
                  {summary.models.map((row) => (
                    <CreditsItem
                      key={row.model}
                      title={`${modelName(row.model)} · ${row.rounds} 轮`}
                      credits={summary.models.length > 1 ? row.credits : null}
                      detail={[
                        `输入 ${formatTokens(row.inputTokens)}`,
                        row.cacheReadTokens > 0 ? `缓存命中 ${formatTokens(row.cacheReadTokens)}` : null,
                        `输出 ${formatTokens(row.outputTokens)}`,
                      ].filter(Boolean).join(' · ')}
                    />
                  ))}
                </CreditsGroup>
              )}
              {summary.tools.length > 0 && (
                <CreditsGroup label="工具" color={TOOL_COLOR} total={summary.toolCredits}>
                  {summary.tools.map((row) => (
                    <CreditsItem
                      key={row.label}
                      title={`${row.label} ×${row.count}`}
                      credits={summary.tools.length > 1 ? row.credits : null}
                    />
                  ))}
                </CreditsGroup>
              )}
            </div>

            {notes.length > 0 && (
              <ul className="space-y-0.5 border-t px-3.5 py-2.5 text-muted-foreground">
                {notes.map((note) => <li key={note}>{note}</li>)}
              </ul>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
