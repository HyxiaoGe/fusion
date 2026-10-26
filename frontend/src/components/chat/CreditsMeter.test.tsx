import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { CreditsSummary } from '@/lib/api/credits';
import CreditsMeter from './CreditsMeter';

function credits(daily: number, weekly: number): CreditsSummary {
  return {
    tier: 'guest',
    unlimited: false,
    daily: { limit: 300, used: daily, remaining: 300 - daily, resets_at: '' },
    weekly: { limit: 1000, used: weekly, remaining: 1000 - weekly, resets_at: '' },
    last_reset_at: null,
  };
}

describe('CreditsMeter', () => {
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
