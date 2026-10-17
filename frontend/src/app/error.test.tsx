import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import i18n from '@/lib/i18n';
import AppError from './error';

const refreshMock = vi.hoisted(() => vi.fn());

vi.mock('next/navigation', () => ({
  useRouter: () => ({ refresh: refreshMock }),
}));

describe('AppError', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh-CN');
  });

  it('重试时先刷新路由数据再重置错误边界，且不向用户展示内部错误信息', () => {
    const reset = vi.fn();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const error = new Error('upstream timeout: fusion-api');

    render(<AppError error={error} reset={reset} />);

    expect(consoleError).toHaveBeenCalledWith(error);
    expect(screen.queryByText(/upstream timeout/)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: i18n.t('errors.crash.title') })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: i18n.t('errors.crash.retry') }));

    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: i18n.t('errors.common.home') })).toHaveAttribute('href', '/');
    consoleError.mockRestore();
  });
});
