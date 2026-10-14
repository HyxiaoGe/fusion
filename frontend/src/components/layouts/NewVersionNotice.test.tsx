import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const scheduler = vi.hoisted(() => ({ run: null as null | (() => void) }));

vi.mock('@/lib/scheduler/backgroundScheduler', () => ({
  registerBackgroundTask: (run: () => void) => {
    scheduler.run = run;
    return () => { scheduler.run = null; };
  },
}));

vi.mock('react-i18next', async () => {
  const { default: messages } = await import('@/lib/i18n/locales/zh-CN.json');
  return { useTranslation: () => ({ t: (key: string) => key.split('.').reduce<unknown>((value, part) =>
    value && typeof value === 'object' ? (value as Record<string, unknown>)[part] : undefined, messages) ?? key }) };
});

function mockServerVersion(version: string) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ version }), { status: 200 })));
}

async function loadNotice() {
  vi.resetModules();
  vi.stubEnv('NEXT_PUBLIC_BUILD_VERSION', 'v1');
  const { NewVersionNotice } = await import('./NewVersionNotice');
  return NewVersionNotice;
}

async function runCheck() {
  await act(async () => {
    scheduler.run?.();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('NewVersionNotice 新版本提示', () => {
  beforeEach(() => { scheduler.run = null; });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it('线上版本与当前一致时不提示', async () => {
    mockServerVersion('v1');
    const Notice = await loadNotice();
    render(<Notice />);
    await runCheck();
    expect(screen.queryByText('有新版本可用')).toBeNull();
  });

  it('线上有新版本时提示，稍后关闭后同一版本不再出现', async () => {
    mockServerVersion('v2');
    const Notice = await loadNotice();
    render(<Notice />);
    await runCheck();
    expect(screen.getByText('有新版本可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '稍后' }));
    expect(screen.queryByText('有新版本可用')).toBeNull();
    await runCheck();
    expect(screen.queryByText('有新版本可用')).toBeNull();

    mockServerVersion('v3');
    await runCheck();
    expect(screen.getByText('有新版本可用')).toBeInTheDocument();
  });
});
