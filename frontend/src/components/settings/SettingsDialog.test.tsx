import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const dispatchMock = vi.hoisted(() => vi.fn());
const useAppSelectorMock = vi.hoisted(() => vi.fn());

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => dispatchMock,
  useAppSelector: useAppSelectorMock,
}));

vi.mock('react-i18next', async () => {
  const { default: messages } = await import('@/lib/i18n/locales/zh-CN.json');
  return { useTranslation: () => ({ t: (key: string) => key.split('.').reduce<unknown>((value, part) =>
    value && typeof value === 'object' ? (value as Record<string, unknown>)[part] : undefined, messages) ?? key }) };
});

vi.mock('@/components/settings/panels/SystemPrompt', () => ({
  default: () => <div>系统提示词设置</div>,
}));

vi.mock('@/components/settings/KnowledgeBaseManager', () => ({
  default: () => <div>知识库管理内容</div>,
}));

vi.mock('@/components/settings/panels/MemoryPanel', () => ({
  default: () => <div>记忆管理面板</div>,
}));

import { SettingsDialog } from './SettingsDialog';

function mockSettingsDialogState(isSuperuser: boolean, activeSettingsTab = 'general', isAuthenticated = false) {
  useAppSelectorMock.mockImplementation((selector) =>
    selector({
      settings: {
        isSettingsDialogOpen: true,
        activeSettingsTab,
      },
      theme: {
        mode: 'system',
      },
      auth: {
        isAuthenticated,
        user: {
          is_superuser: isSuperuser,
        },
      },
    })
  );
}

describe('SettingsDialog 页签', () => {
  beforeEach(() => {
    dispatchMock.mockReset();
    useAppSelectorMock.mockReset();
  });

  it('普通用户不显示服务用量页签', () => {
    mockSettingsDialogState(false);

    render(<SettingsDialog />);

    expect(screen.queryByRole('tab', { name: /服务用量/ })).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(2);
  });

  it('主题选中和关闭操作沿用现有状态协议', () => {
    mockSettingsDialogState(false);
    render(<SettingsDialog />);
    expect(screen.getByRole('button', { name: '跟随系统' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '浅色模式' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: '深色模式' }));
    expect(dispatchMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'theme/setThemeMode', payload: 'dark' }));
    expect(screen.getByRole('tablist', { name: '设置分类' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭设置' }));
    expect(dispatchMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'settings/closeSettingsDialog' }));
  });

  it('普通用户可以看到知识库页签', () => {
    mockSettingsDialogState(false);

    render(<SettingsDialog />);

    expect(screen.getByRole('tab', { name: /知识库/ })).toBeInTheDocument();
    expect(screen.queryByText('知识库管理内容')).toBeNull();
  });

  it('知识库页签复用统一管理面板', () => {
    mockSettingsDialogState(false, 'knowledge');

    render(<SettingsDialog />);

    expect(screen.getByText('知识库管理内容')).toBeInTheDocument();
  });

  it('普通用户残留服务用量选中状态时回退到常规设置', () => {
    mockSettingsDialogState(false, 'usage');

    render(<SettingsDialog />);

    expect(screen.queryByRole('tab', { name: /服务用量/ })).toBeNull();
    expect(screen.getByRole('tabpanel', { name: /常规设置/ })).toBeInTheDocument();
  });

  it('普通用户残留 MCP 服务选中状态时回退到常规设置', () => {
    mockSettingsDialogState(false, 'mcp-servers');

    render(<SettingsDialog />);

    expect(screen.queryByRole('tab', { name: /MCP 服务/ })).toBeNull();
    expect(screen.getByRole('tabpanel', { name: /常规设置/ })).toBeInTheDocument();
  });

  it('普通用户残留模型管理选中状态时回退到常规设置', () => {
    mockSettingsDialogState(false, 'model-management');

    render(<SettingsDialog />);

    expect(screen.queryByRole('tab', { name: /模型管理/ })).toBeNull();
    expect(screen.getByRole('tabpanel', { name: /常规设置/ })).toBeInTheDocument();
  });

  it('登录用户可以看到记忆页签并打开记忆面板', () => {
    mockSettingsDialogState(false, 'memory', true);

    render(<SettingsDialog />);

    expect(screen.getByRole('tab', { name: /记忆/ })).toBeInTheDocument();
    expect(screen.getByText('记忆管理面板')).toBeInTheDocument();
  });

  it('未登录时不显示记忆页签，残留选中状态回退到常规设置', () => {
    mockSettingsDialogState(false, 'memory');

    render(<SettingsDialog />);

    expect(screen.queryByRole('tab', { name: /记忆/ })).toBeNull();
    expect(screen.getByRole('tabpanel', { name: /常规设置/ })).toBeInTheDocument();
  });

  it('管理员设置弹窗只保留个人设置，管理功能已移到管理中心', () => {
    mockSettingsDialogState(true, 'mcp-servers', true);

    render(<SettingsDialog />);

    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toHaveLength(3);
    expect(screen.queryByRole('tab', { name: /服务用量|MCP 服务|模型管理|运行时配置/ })).toBeNull();
    expect(screen.getByRole('tabpanel', { name: /常规设置/ })).toBeInTheDocument();
  });
});
