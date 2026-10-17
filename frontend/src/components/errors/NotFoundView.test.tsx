import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import i18n from '@/lib/i18n';
import NotFoundView from './NotFoundView';

const routerMock = vi.hoisted(() => ({ back: vi.fn(), push: vi.fn() }));
const pathnameMock = vi.hoisted(() => ({ value: '/chat/%E4%B8%A2%E5%A4%B1' }));

vi.mock('next/navigation', () => ({
  useRouter: () => routerMock,
  usePathname: () => pathnameMock.value,
}));

function mockReducedMotion(reduce: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: reduce && query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

describe('NotFoundView', () => {
  const lead = () => i18n.t('errors.notFound.lead');
  const tail = () => i18n.t('errors.notFound.tail');

  beforeAll(async () => {
    await i18n.changeLanguage('zh-CN');
  });

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    pathnameMock.value = '/chat/%E4%B8%A2%E5%A4%B1';
    routerMock.back.mockReset();
    routerMock.push.mockReset();
    mockReducedMotion(false);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('逐字打出解码后的路径，并在卸载时清理定时器', () => {
    const { container, unmount } = render(<NotFoundView />);
    const visual = container.querySelector('p > span[aria-hidden="true"]');

    expect(screen.getByText(`${lead()} /chat/丢失 ${tail()}`)).toBeInTheDocument();
    expect(visual?.textContent).toBe('');

    act(() => {
      vi.advanceTimersByTime(400 + 32 * 3);
    });
    expect(visual?.textContent).toBe(lead().slice(0, 3));

    act(() => {
      vi.advanceTimersByTime(32 * (lead().length + '/chat/丢失'.length + tail().length));
    });
    expect(visual?.textContent).toBe(`${lead()}/chat/丢失${tail()}`);
    expect(screen.getByText('/chat/丢失').tagName).toBe('CODE');

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('减少动态效果时直接显示完整文案', () => {
    mockReducedMotion(true);
    const { container } = render(<NotFoundView />);
    const visual = container.querySelector('p > span[aria-hidden="true"]');

    expect(visual?.textContent).toBe(`${lead()}/chat/丢失${tail()}`);
  });

  it('非法编码的路径按原样展示', () => {
    pathnameMock.value = '/bad/%E0%A4%A';
    render(<NotFoundView />);

    expect(screen.getByText(`${lead()} /bad/%E0%A4%A ${tail()}`)).toBeInTheDocument();
  });

  it('没有浏览历史时返回上一页会回到首页', () => {
    render(<NotFoundView />);
    fireEvent.click(screen.getByRole('button', { name: i18n.t('errors.notFound.back') }));

    expect(routerMock.back).not.toHaveBeenCalled();
    expect(routerMock.push).toHaveBeenCalledWith('/');
    expect(screen.getByRole('link', { name: i18n.t('errors.common.home') })).toHaveAttribute('href', '/');
  });
});
