import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchConversationCredits, spendResetCard, type CreditsSummary } from '@/lib/api/credits';
import { ApiError } from '@/types/api';
import CreditsMeter from './CreditsMeter';

vi.mock('@/lib/api/credits', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/credits')>()),
  fetchConversationCredits: vi.fn(),
  spendResetCard: vi.fn(),
}));

const fetchConversationCreditsMock = vi.mocked(fetchConversationCredits);
const spendResetCardMock = vi.mocked(spendResetCard);

function credits(daily: number, weekly: number): CreditsSummary {
  return {
    tier: 'guest',
    unlimited: false,
    daily: { limit: 300, used: daily, remaining: 300 - daily, resets_at: '' },
    weekly: { limit: 1000, used: weekly, remaining: 1000 - weekly, resets_at: '' },
    last_reset_at: null,
    reset_cards: { available: 0, next_expires_at: null },
  };
}

describe('CreditsMeter', () => {
  beforeEach(() => {
    fetchConversationCreditsMock.mockReset();
    spendResetCardMock.mockReset();
  });

  it('有重置卡时显示张数与最早到期日，二次确认后使用并通知刷新额度', async () => {
    spendResetCardMock.mockResolvedValue(credits(0, 0));
    const onCreditsChanged = vi.fn();
    render(
      <CreditsMeter
        credits={{ ...credits(300, 900), reset_cards: { available: 2, next_expires_at: '2026-11-25T07:00:00+00:00' } }}
        isGuest={false}
        onLogin={vi.fn()}
        onCreditsChanged={onCreditsChanged}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '额度已用 100%' }));

    const section = screen.getByTestId('credits-meter-reset-cards');
    expect(section.textContent).toContain('重置卡2 张');
    expect(section.textContent).toContain('最早一张 11月25日到期');

    fireEvent.click(screen.getByRole('button', { name: '使用一张重置卡' }));
    expect(spendResetCardMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认使用' }));

    await waitFor(() => expect(onCreditsChanged).toHaveBeenCalledTimes(1));
    expect(spendResetCardMock).toHaveBeenCalledTimes(1);
  });

  it('使用被拒绝时显示服务端原因；没有卡或不限额时不显示重置卡', async () => {
    spendResetCardMock.mockRejectedValue(new ApiError('CONFLICT', '当前没有可重置的用量', 'req'));
    const onCreditsChanged = vi.fn();
    const { unmount } = render(
      <CreditsMeter
        credits={{ ...credits(0, 0), reset_cards: { available: 1, next_expires_at: '2026-11-25T07:00:00+00:00' } }}
        isGuest={false}
        onLogin={vi.fn()}
        onCreditsChanged={onCreditsChanged}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '额度已用 0%' }));
    fireEvent.click(screen.getByRole('button', { name: '使用一张重置卡' }));
    fireEvent.click(screen.getByRole('button', { name: '确认使用' }));

    await waitFor(() => expect(screen.getByTestId('credits-meter-reset-cards').textContent).toContain('当前没有可重置的用量'));
    expect(onCreditsChanged).not.toHaveBeenCalled();
    unmount();

    const { unmount: unmountEmpty } = render(<CreditsMeter credits={credits(10, 10)} isGuest={false} onLogin={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '额度已用 3%' }));
    expect(screen.queryByTestId('credits-meter-reset-cards')).toBeNull();
    unmountEmpty();

    render(
      <CreditsMeter
        credits={{ ...credits(10, 10), unlimited: true, reset_cards: { available: 1, next_expires_at: '2026-11-25T07:00:00+00:00' } }}
        isGuest={false}
        onLogin={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '额度不限' }));
    expect(screen.queryByTestId('credits-meter-reset-cards')).toBeNull();
  });

  it('打开着对话时，点开才拉这个对话的累计消耗，并提示还没结算的生成', async () => {
    fetchConversationCreditsMock.mockResolvedValue({
      conversation_id: 'conv-1',
      credits: 445.01,
      runs: 2,
      pending_runs: 1,
    });
    render(<CreditsMeter credits={credits(60, 900)} isGuest={false} onLogin={vi.fn()} conversationId="conv-1" />);
    expect(fetchConversationCreditsMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '额度已用 90%' }));

    const row = screen.getByTestId('credits-meter-conversation');
    expect(row.textContent).toContain('计算中');
    await waitFor(() => expect(row.textContent).toContain('本对话已用445'));
    expect(row.textContent).toContain('还有 1 次生成正在结算');
    expect(fetchConversationCreditsMock).toHaveBeenCalledWith('conv-1', expect.any(AbortSignal));
  });

  it('没有打开对话时不显示本对话一行，也不请求', () => {
    render(<CreditsMeter credits={credits(60, 900)} isGuest={false} onLogin={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '额度已用 90%' }));

    expect(screen.queryByTestId('credits-meter-conversation')).toBeNull();
    expect(fetchConversationCreditsMock).not.toHaveBeenCalled();
  });

  it('对话消耗拉取失败时显示暂不可用，不影响日、周额度', async () => {
    fetchConversationCreditsMock.mockRejectedValue(new Error('boom'));
    render(<CreditsMeter credits={credits(60, 900)} isGuest={false} onLogin={vi.fn()} conversationId="conv-1" />);
    fireEvent.click(screen.getByRole('button', { name: '额度已用 90%' }));

    await waitFor(() => expect(screen.getByTestId('credits-meter-conversation').textContent).toContain('暂不可用'));
    expect(screen.getByTestId('credits-meter-panel').textContent).toContain('今日60 / 300');
  });

  it('环按用得更满的窗口显示百分比，点开看日、周总额度与已用量', () => {
    render(<CreditsMeter credits={credits(60, 900)} isGuest onLogin={vi.fn()} />);

    const trigger = screen.getByRole('button', { name: '额度已用 90%' });
    fireEvent.click(trigger);

    const panel = screen.getByTestId('credits-meter-panel');
    expect(panel.textContent).toContain('今日60 / 300');
    expect(panel.textContent).toContain('剩余 240');
    expect(panel.textContent).toContain('本周900 / 1,000');
    expect(panel.textContent).toContain('下周一 0 点恢复');
  });

  it('超额时百分比封顶、显示已用完；游客可从面板登录，登录用户没有登录入口', () => {
    const onLogin = vi.fn();
    const { unmount } = render(
      <CreditsMeter credits={{ ...credits(320, 320), daily: { limit: 300, used: 320, remaining: -20, resets_at: '' } }} isGuest onLogin={onLogin} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '额度已用 100%' }));
    expect(screen.getByTestId('credits-meter-panel').textContent).toContain('已用完');
    fireEvent.click(screen.getByRole('button', { name: '登录获取更多额度' }));
    expect(onLogin).toHaveBeenCalledTimes(1);
    unmount();

    render(<CreditsMeter credits={{ ...credits(0, 0), tier: 'user' }} isGuest={false} onLogin={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '额度已用 0%' }));
    expect(screen.queryByRole('button', { name: '登录获取更多额度' })).toBeNull();
  });

  it('不限额账号显示无穷符号，只列今日、本周已用量', () => {
    render(<CreditsMeter credits={{ ...credits(1234, 5678), tier: 'admin', unlimited: true }} isGuest={false} onLogin={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '额度不限' }));

    const panel = screen.getByTestId('credits-meter-panel');
    expect(panel.textContent).toContain('不限额');
    expect(panel.textContent).toContain('今日已用1,234');
    expect(panel.textContent).toContain('本周已用5,678');
    expect(panel.textContent).not.toContain('恢复');
  });
});
