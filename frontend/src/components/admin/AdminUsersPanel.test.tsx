import i18n from '@/lib/i18n';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/types/api';
import { adminNavigation } from '@/test/adminNavigation';
import { renderAdmin } from '@/test/renderAdmin';

const apiMocks = vi.hoisted(() => ({
  getAdminUsers: vi.fn(),
}));

vi.mock('@/lib/api/adminAudit', () => apiMocks);
vi.mock('next/navigation', async () => (await import('@/test/adminNavigation')).nextNavigationMock);
vi.mock('next/link', async () => (await import('@/test/adminNavigation')).nextLinkMock);

import AdminUsersPanel from './AdminUsersPanel';

const users = [
  {
    id: 'user-alpha', username: 'alpha', nickname: '同名用户', email_masked: 's***@example.com',
    is_superuser: false, is_guest: false, created_at: null, updated_at: null, last_active_at: null,
    conversation_count: 1, question_count: 2, tool_call_count: 3, input_tokens: 4, output_tokens: 5,
  },
  {
    id: 'user-beta', username: 'beta', nickname: '同名用户', email_masked: 's***@example.com',
    is_superuser: false, is_guest: false, created_at: null, updated_at: null, last_active_at: null,
    conversation_count: 1, question_count: 2, tool_call_count: 3, input_tokens: 4, output_tokens: 5,
  },
];

const page = {
  items: users, total: 60, page: 1, page_size: 25, total_pages: 3, has_next: true, has_prev: false,
};

beforeEach(() => {
  void i18n.changeLanguage('zh-CN');
  apiMocks.getAdminUsers.mockReset().mockResolvedValue(page);
  adminNavigation.reset('/admin/users');
});

describe('AdminUsersPanel', () => {
  it('英文可见标签与可访问名称一致', async () => {
    await i18n.changeLanguage('en-US');
    renderAdmin(<AdminUsersPanel />);
    expect(screen.getByRole('textbox', { name: 'Search users' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Account type' })).toBeInTheDocument();
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
    fireEvent.change(screen.getByLabelText('账号类型'), { target: { value: 'guest' } });
    fireEvent.change(screen.getByLabelText('注册开始日期'), { target: { value: '2026-07-01' } });
    fireEvent.change(screen.getByLabelText('注册结束日期'), { target: { value: '2026-07-14' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));

    expect(adminNavigation.url).toBe('/admin/users?q=beta&account_type=guest&created_from=2026-07-01&created_to=2026-07-14');
    expect(adminNavigation.push).not.toHaveBeenCalled();
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith({
      page: 1, page_size: 25, q: 'beta', is_guest: true, created_from: '2026-07-01', created_to: '2026-07-14',
    }, expect.any(AbortSignal)));

    fireEvent.click(screen.getByRole('button', { name: '重置筛选' }));

    expect(adminNavigation.url).toBe('/admin/users');
    await waitFor(() => expect(screen.getByLabelText('搜索用户')).toHaveValue(''));
    expect(screen.getByLabelText('账号类型')).toHaveValue('');
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith({
      page: 1, page_size: 25, q: '', created_from: '', created_to: '',
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

  it.each([
    ['admin', { is_superuser: true }],
    ['registered', { is_guest: false }],
  ])('账号类型 %s 映射到接口条件', async (accountType, expected) => {
    adminNavigation.reset(`/admin/users?account_type=${accountType}`);
    renderAdmin(<AdminUsersPanel />);
    await waitFor(() => expect(apiMocks.getAdminUsers).toHaveBeenLastCalledWith(
      expect.objectContaining(expected), expect.any(AbortSignal),
    ));
  });

  it('查看对话直接进入按该用户筛选的对话列表', async () => {
    renderAdmin(<AdminUsersPanel />);
    const link = await screen.findByRole('link', { name: '查看用户对话 user-alpha' });
    expect(link).toHaveAttribute('href', '/admin/conversations?user_id=user-alpha');

    fireEvent.click(link);
    expect(adminNavigation.push).toHaveBeenCalledWith('/admin/conversations?user_id=user-alpha');
  });

  it('列表 403 时卸载内容并显示权限失效', async () => {
    apiMocks.getAdminUsers.mockRejectedValue(new ApiError('FORBIDDEN', '需要管理员权限', 'req-1'));
    renderAdmin(<AdminUsersPanel />);

    expect(await screen.findByText('管理员权限已失效')).toBeInTheDocument();
    expect(screen.queryByLabelText('搜索用户')).toBeNull();
  });
});
