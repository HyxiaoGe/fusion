import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { CreditsEntry, MessageCredits as MessageCreditsData } from '@/lib/api/credits';
import MessageCredits from './MessageCredits';
import { summarizeMessageCredits } from './messageCreditsModel';

const { fetchMessageCreditsMock } = vi.hoisted(() => ({ fetchMessageCreditsMock: vi.fn() }));

vi.mock('@/lib/api/credits', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/credits')>()),
  fetchMessageCredits: fetchMessageCreditsMock,
}));

vi.mock('@/redux/hooks', () => ({
  useAppSelector: (selector: (state: unknown) => unknown) =>
    selector({ models: { models: [{ id: 'mimo-v2.6-flash', name: 'MiMo Flash' }] } }),
}));

function entry(overrides: Partial<CreditsEntry> = {}): CreditsEntry {
  return {
    id: 'e1',
    kind: 'run',
    run_id: 'run-1',
    conversation_id: 'c1',
    message_id: 'm1',
    model_id: 'auto',
    run_status: 'completed',
    charged: true,
    credits: 321.4,
    occurred_at: '2026-10-26T05:00:00+00:00',
    breakdown: {
      model_credits: 21.4,
      tool_credits: 300,
      rounds: [
        { model: 'mimo-v2.6-flash', input_tokens: 13312, output_tokens: 137, cache_read_tokens: 0, credits: 20.3, off_peak: false, unpriced: false },
        { model: 'mimo-v2.6-flash', input_tokens: 13594, output_tokens: 74, cache_read_tokens: 13248, credits: 1.1, off_peak: true, unpriced: false },
      ],
      tools: [
        { tool: 'generate_image', label: 'generate_image', count: 1, unit_credits: 300, credits: 300 },
        { tool: 'load_skill', label: 'load_skill', count: 1, unit_credits: 0, credits: 0 },
        { tool: 'mcp:amap', label: '高德地图 / maps_direction', count: 2, unit_credits: 2, credits: 4 },
      ],
    },
    ...overrides,
  };
}

describe('summarizeMessageCredits', () => {
  it('按模型合并轮次、工具用中文名，出错的生成只计数不计金额', () => {
    const data: MessageCreditsData = {
      message_id: 'm1',
      credits: 321.4,
      pending_runs: 1,
      entries: [entry(), entry({ id: 'e2', run_id: 'run-2', charged: false, credits: 0, run_status: 'error' })],
    };

    const summary = summarizeMessageCredits(data);

    expect(summary.models).toEqual([
      { model: 'mimo-v2.6-flash', rounds: 2, inputTokens: 26906, cacheReadTokens: 13248, outputTokens: 211, credits: expect.closeTo(21.4, 5) },
    ]);
    expect(summary.tools).toEqual([
      { label: '生成图片', count: 1, credits: 300 },
      { label: '高德地图 / maps_direction', count: 2, credits: 4 },
    ]);
    expect(summary).toMatchObject({ credits: 321.4, runs: 2, uncharged: 1, pending: 1, offPeak: true });
  });
});

describe('MessageCredits', () => {
  beforeEach(() => fetchMessageCreditsMock.mockReset());

  it('打开时才拉明细，并展示模型、工具与说明', async () => {
    fetchMessageCreditsMock.mockResolvedValue({ message_id: 'm1', credits: 321.4, pending_runs: 0, entries: [entry()] });
    render(<MessageCredits messageId="m1" />);
    expect(fetchMessageCreditsMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '查看消耗' }));

    const panel = await screen.findByTestId('message-credits');
    await waitFor(() => expect(panel.textContent).toContain('321 credits'));
    expect(fetchMessageCreditsMock).toHaveBeenCalledWith('m1', expect.any(AbortSignal));
    expect(panel.textContent).toContain('MiMo Flash · 2 轮');
    expect(panel.textContent).toContain('缓存命中 13k');
    expect(panel.textContent).toContain('生成图片 ×1');
    expect(panel.textContent).toContain('部分轮次按空闲时段价计算');
  });

  it('还没结算时提示稍后再看，加载失败给出可理解的提示', async () => {
    fetchMessageCreditsMock.mockResolvedValueOnce({ message_id: 'm1', credits: 0, pending_runs: 1, entries: [] });
    const { unmount } = render(<MessageCredits messageId="m1" />);
    fireEvent.click(screen.getByRole('button', { name: '查看消耗' }));
    expect(await screen.findByText('正在结算，稍后再看')).toBeTruthy();
    unmount();

    fetchMessageCreditsMock.mockRejectedValueOnce(new Error('boom'));
    render(<MessageCredits messageId="m2" />);
    fireEvent.click(screen.getByRole('button', { name: '查看消耗' }));
    expect(await screen.findByText('消耗明细加载失败，请稍后再试')).toBeTruthy();
  });
});
