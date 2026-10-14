import i18n from '@/lib/i18n';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/types/api';
import { adminNavigation } from '@/test/adminNavigation';
import { renderAdmin } from '@/test/renderAdmin';

const apiMocks = vi.hoisted(() => ({
  getAdminUsers: vi.fn(),
  getAdminUser: vi.fn(),
}));

vi.mock('@/lib/api/adminAudit', () => apiMocks);
vi.mock('next/navigation', async () => (await import('@/test/adminNavigation')).nextNavigationMock);
vi.mock('next/link', async () => (await import('@/test/adminNavigation')).nextLinkMock);

import AdminUsersPanel from './AdminUsersPanel';
import AdminUserDetailView from './AdminUserDetailView';

const users = [
  {
    id: 'user-alpha', username: 'alpha', nickname: '同名用户', email_masked: 's***@example.com',
    is_superuser: false, created_at: null, updated_at: null, last_active_at: null,
    conversation_count: 1, message_count: 2, tool_call_count: 3, input_tokens: 4, output_tokens: 5,
  },
  {
    id: 'user-beta', username: 'beta', nickname: '同名用户', email_masked: 's***@example.com',
    is_superuser: false, created_at: null, updated_at: null, last_active_at: null,
    conversation_count: 1, message_count: 2, tool_call_count: 3, input_tokens: 4, output_tokens: 5,
  },
];

const page = {
  items: users, total: 60, page: 1, page_size: 25, total_pages: 3, has_next: true, has_prev: false,
};

beforeEach(() => {
  void i18n.changeLanguage('zh-CN');
  apiMocks.getAdminUsers.mockReset().mockResolvedValue(page);
  apiMocks.getAdminUser.mockReset();
  adminNavigation.reset('/admin/users');
});

describe('AdminUsersPanel', () => {
  it('英文可见标签与可访问名称一致', async () => {
    await i18n.changeLanguage('en-US');
    renderAdmin(<AdminUsersPanel />);
    expect(screen.getByRole('textbox', { name: 'Search users' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'User permissions' })).toBeInTheDocument();
    expect(screen.getByLabelText('Registered from')).toBeInTheDocument();
    expect(screen.getByLabelText('Registered to')).toBeInTheDocument();
    expect(screen.getByText('Search users', { selector: 'span' })).toBeInTheDocument();
  });

  it('同昵称同脱敏邮箱时仍稳定展示 username 与 user_id', async () => {
    renderAdmin(<AdminUsersPanel />);

    await screen.findAllByText('同名用户');
    expect(screen.getByText('@alpha')).toBeInTheDocument();
    expect(screen.getByText('@beta')).toBeInTheDocument();
    expect(screen.getByText('user-alpha')).toBeInTheDocument();
    expect(screen.getByText('user-beta')).toBeInTheDocument();
    expect(screen.getAllByText('s***@example.com')).toHaveLength(2);
  });

  it('筛选写入 URL 并据此请求，重置清空 URL 与输入框', async () => {
    renderAdmin(<AdminUsersPanel />);
    await screen.findByText('@alpha');

    fireEvent.change(screen.getByLabelText('搜索用户'), { target: { value: ' beta ' } });
    fireEvent.change(screen.getByLabelText('管理员筛选'), { target: { value: 'true' } });
    fireEvent.change(screen.getByLabelText('注册开始日期'), { target: { value: '2026-07-01' } });
    fireEvent.change(screen.getByLabelText('注册结束日期'), { target: { value: '2026-07-14' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));

    expect(adminNavigation.url).toBe('/admin/users?q=beta&is_superuser=true&created_from=2026-07-01&created_to=2026-07-14');
    expect(adminNavigation.push).not.toHaveBeenCalled();
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith({
      page: 1, page_size: 25, q: 'beta', is_superuser: true, created_from: '2026-07-01', created_to: '2026-07-14',
    }, expect.any(AbortSignal)));

    fireEvent.click(screen.getByRole('button', { name: '重置筛选' }));

    expect(adminNavigation.url).toBe('/admin/users');
    await waitFor(() => expect(screen.getByLabelText('搜索用户')).toHaveValue(''));
    expect(screen.getByLabelText('管理员筛选')).toHaveValue('');
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith({
      page: 1, page_size: 25, q: '', is_superuser: undefined, created_from: '', created_to: '',
    }, expect.any(AbortSignal)));
  });

  it('从 URL 恢复筛选和页码，翻页保留筛选草稿', async () => {
    adminNavigation.reset('/admin/users?q=alpha&page=2');
    apiMocks.getAdminUsers.mockResolvedValue({ ...page, page: 2, has_prev: true });
    renderAdmin(<AdminUsersPanel />);

    expect(screen.getByLabelText('搜索用户')).toHaveValue('alpha');
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2, q: 'alpha' }), expect.any(AbortSignal),
    ));

    await screen.findByText('@alpha');
    fireEvent.change(screen.getByLabelText('搜索用户'), { target: { value: 'alp' } });
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));

    expect(adminNavigation.url).toBe('/admin/users?q=alpha&page=3');
    expect(screen.getByLabelText('搜索用户')).toHaveValue('alp');
  });

  it('查看详情是指向用户详情路径的链接', async () => {
    renderAdmin(<AdminUsersPanel />);
    const link = await screen.findByRole('link', { name: '查看用户详情 user-alpha' });
    expect(link).toHaveAttribute('href', '/admin/users/user-alpha');

    fireEvent.click(link);
    expect(adminNavigation.push).toHaveBeenCalledWith('/admin/users/user-alpha');
  });

  it('列表 403 时卸载内容并显示权限失效', async () => {
    apiMocks.getAdminUsers.mockRejectedValue(new ApiError('FORBIDDEN', '需要管理员权限', 'req-1'));
    renderAdmin(<AdminUsersPanel />);

    expect(await screen.findByText('管理员权限已失效')).toBeInTheDocument();
    expect(screen.queryByLabelText('搜索用户')).toBeNull();
  });
});

describe('AdminUserDetailView', () => {
  it('展示身份与注册信息，并链接到该用户的对话', async () => {
    apiMocks.getAdminUser.mockResolvedValue({ ...users[0], email: 'alpha@example.com', system_prompt: null });
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);

    const detail = await screen.findByLabelText('用户详情 user-alpha');
    expect(detail).toHaveTextContent('alpha@example.com');
    expect(detail).toHaveTextContent('@alpha');
    expect(screen.getByRole('link', { name: '查看该用户的对话' })).toHaveAttribute('href', '/admin/conversations?user_id=user-alpha');
  });

  it('首次失败可重试成功', async () => {
    apiMocks.getAdminUser
      .mockRejectedValueOnce(new Error('用户详情暂时不可用'))
      .mockResolvedValueOnce({ ...users[0], email: 'alpha@example.com', system_prompt: null });
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);

    expect(await screen.findByText('用户详情暂时不可用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试用户详情' }));

    expect(await screen.findByText('alpha@example.com')).toBeInTheDocument();
    expect(apiMocks.getAdminUser).toHaveBeenCalledTimes(2);
  });

  it.each([null, '', '   '])('提示词为 %p 时完全隐藏区块', async systemPrompt => {
    apiMocks.getAdminUser.mockResolvedValueOnce({ ...users[0], email: 'alpha@example.com', system_prompt: systemPrompt });
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);
    await screen.findByText('alpha@example.com');
    expect(screen.queryByText('自定义提示词')).toBeNull();
  });

  it('非空提示词使用中文标题展示', async () => {
    apiMocks.getAdminUser.mockResolvedValueOnce({ ...users[0], email: 'alpha@example.com', system_prompt: '请使用简洁中文回答' });
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);
    expect(await screen.findByText('自定义提示词')).toBeInTheDocument();
    expect(screen.getByText('请使用简洁中文回答')).toBeInTheDocument();
  });

  it('直接深链进入时返回按钮替换到用户列表', async () => {
    adminNavigation.reset('/admin/users/user-alpha');
    apiMocks.getAdminUser.mockResolvedValue({ ...users[0], email: 'alpha@example.com', system_prompt: null });
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);

    fireEvent.click(screen.getByRole('button', { name: '返回用户列表' }));
    expect(adminNavigation.back).not.toHaveBeenCalled();
    expect(adminNavigation.url).toBe('/admin/users');
  });

  it('详情 403 时清空内容并显示权限失效', async () => {
    apiMocks.getAdminUser.mockRejectedValue(new ApiError('FORBIDDEN', '需要管理员权限', 'req-2'));
    renderAdmin(<AdminUserDetailView userId="user-alpha" />);

    expect(await screen.findByText('管理员权限已失效')).toBeInTheDocument();
    expect(adminNavigation.url).toBe('/admin/users');
  });
});
