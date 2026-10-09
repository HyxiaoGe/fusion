import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { configureStore } from '@reduxjs/toolkit';
import { Provider } from 'react-redux';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import authReducer from '@/redux/slices/authSlice';
import settingsReducer from '@/redux/slices/settingsSlice';
import themeReducer from '@/redux/slices/themeSlice';
import { SettingsDialog } from './SettingsDialog';
import { SettingsDialogFocusProvider } from './SettingsDialogFocusContext';
import { UserAvatarMenu } from '@/components/layouts/UserAvatarMenu';
import { UserMenu } from '@/components/layouts/UserMenu';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/hooks/useHasMounted', () => ({ useHasMounted: () => true }));
vi.mock('@/components/auth/LoginDialog', () => ({ LoginDialog: () => null }));
vi.mock('@/components/settings/panels/SystemPrompt', () => ({ default: () => <div>个性化设置</div> }));
vi.mock('@/components/settings/panels/DataManagement', () => ({ default: () => null }));
vi.mock('@/components/settings/KnowledgeBaseManager', () => ({ default: () => null }));
vi.mock('@/components/settings/panels/McpServerManager', () => ({ default: () => null }));
vi.mock('@/components/settings/panels/ModelManagementPanel', () => ({ default: () => null }));
vi.mock('@/components/settings/panels/RuntimeConfigManager', () => ({ default: () => null }));
vi.mock('@/components/settings/panels/ServiceUsagePanel', () => ({ default: () => null }));
vi.mock('react-i18next', async () => {
  const { default: messages } = await import('@/lib/i18n/locales/zh-CN.json');
  return { useTranslation: () => ({ t: (key: string) => key.split('.').reduce<unknown>((value, part) =>
    value && typeof value === 'object' ? (value as Record<string, unknown>)[part] : undefined, messages) ?? key }) };
});

beforeAll(() => {
  window.PointerEvent = MouseEvent as typeof PointerEvent;
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});

describe('真实头像入口与设置弹窗的焦点交接', () => {
  it('两个入口依次打开设置，关闭后分别返回本次入口', async () => {
    const user = userEvent.setup();
    const authState = authReducer(undefined, { type: '测试初始化' });
    // 只建立测试登录态，不访问认证接口或持久化用户数据。
    const store = configureStore({
      reducer: { auth: authReducer, settings: settingsReducer, theme: themeReducer },
      preloadedState: {
        auth: { ...authState, isAuthenticated: true, sessionResolved: true, status: 'succeeded' as const,
          user: { id: 'focus-user', username: 'test', nickname: '测试用户', avatar: null, email: null, mobile: null, system_prompt: '', is_superuser: false } },
      },
    });
    render(<Provider store={store}><SettingsDialogFocusProvider>
      <div data-testid="sidebar-entry"><UserAvatarMenu /></div>
      <div data-testid="header-entry"><UserMenu /></div>
      <SettingsDialog />
    </SettingsDialogFocusProvider></Provider>);

    for (const id of ['sidebar-entry', 'header-entry']) {
      const opener = within(screen.getByTestId(id)).getByRole('button');
      opener.focus();
      await user.keyboard('{Enter}');
      await user.click(await screen.findByRole('menuitem', { name: '设置' }));
      const dialog = await screen.findByRole('dialog', { name: '设置' });
      await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
      expect(opener).not.toHaveFocus();
      await user.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog', { name: '设置' })).toBeNull());
      await waitFor(() => expect(opener).toHaveFocus());
    }
    expect(store.getState().auth.isAuthenticated).toBe(true);
  });
});
