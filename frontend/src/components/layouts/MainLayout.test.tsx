import React from 'react';
import { flushSync } from 'react-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MainLayout from './MainLayout';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

const { useAppSelectorMock, usePathnameMock } = vi.hoisted(() => ({
  useAppSelectorMock: vi.fn(),
  usePathnameMock: vi.fn(),
}));

vi.mock('@/redux/hooks', () => ({
  useAppSelector: useAppSelectorMock,
  useAppDispatch: () => vi.fn(),
}));

vi.mock('next/navigation', () => ({
  usePathname: usePathnameMock,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('../ui/error-toast', () => ({
  default: () => null,
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock('@/components/auth/LoginDialog', () => ({
  LoginDialog: () => null,
}));

describe('MainLayout', () => {
  beforeEach(() => {
    useAppSelectorMock.mockImplementation((selector: (state: any) => unknown) =>
      selector({
        theme: { mode: 'light' },
        settings: {},
        auth: { isAuthenticated: false, user: null },
      }),
    );
    usePathnameMock.mockReturnValue('/chat/test');
    document.documentElement.className = '';
  });

  const renderLayout = () =>
    render(
      <MainLayout sidebar={<div>Sidebar Content</div>} title="Header Title">
        <div>Main Content</div>
      </MainLayout>,
    );

  it('shows the sidebar behind a drawer toggle on narrow viewports', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 390 });

    renderLayout();

    expect(screen.getByRole('button', { name: '打开对话侧栏' })).toBeTruthy();
    expect(screen.queryByText('Sidebar Content')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '打开对话侧栏' }));

    expect(screen.getByText('Sidebar Content')).toBeTruthy();
    expect(screen.getByRole('button', { name: '收起对话侧栏' })).toBeTruthy();
  });

  it('renders the sidebar inline on desktop viewports', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1280 });

    renderLayout();

    expect(screen.queryByRole('button', { name: '打开对话侧栏' })).toBeNull();
    expect(screen.getByText('Sidebar Content')).toBeTruthy();
  });

  it.each([1, 0])('同一路径的 Portal 通知点击收起遮罩并执行导航（detail=%s）', (detail) => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 390 });
    const navigate = vi.fn();
    render(
      <MainLayout sidebar={(
        <>
          <div>Sidebar Content</div>
          <Popover>
            <PopoverTrigger asChild><button type="button">通知入口</button></PopoverTrigger>
            <PopoverContent aria-label="通知记录">
              <button type="button">未读筛选</button>
              <button type="button" data-sidebar-navigation onClick={navigate}
                // 模拟浏览器在捕获与冒泡之间提交离散更新，避免 act 批处理掩盖提前卸载。
                onClickCapture={() => flushSync(() => undefined)}>
                <span>定位当前结果</span>
              </button>
            </PopoverContent>
          </Popover>
        </>
      )}>
        <div>Main Content</div>
      </MainLayout>,
    );
    fireEvent.click(screen.getByRole('button', { name: '打开对话侧栏' }));
    fireEvent.click(screen.getByRole('button', { name: '通知入口' }));
    const dialog = screen.getByRole('dialog', { name: '通知记录' });
    expect(screen.getByRole('complementary').contains(dialog)).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: '未读筛选' }));
    expect(screen.getByText('Sidebar Content')).toBeTruthy();
    fireEvent.click(screen.getByText('定位当前结果'), { detail });

    expect(navigate).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Sidebar Content')).toBeNull();
    expect(screen.queryByRole('dialog', { name: '通知记录' })).toBeNull();
    expect(usePathnameMock()).toBe('/chat/test');
  });
});
