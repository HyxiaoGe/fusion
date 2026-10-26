import i18n from '@/lib/i18n';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/types/api';
import { adminNavigation } from '@/test/adminNavigation';
import { renderAdmin } from '@/test/renderAdmin';

const apiMocks = vi.hoisted(() => ({
  getAdminUsers: vi.fn(),
}));

const creditsMocks = vi.hoisted(() => ({
  fetchAdminUserCredits: vi.fn(),
  resetAdminUserCredits: vi.fn(),
  grantAdminResetCards: vi.fn(),
  fetchCreditsResetAll: vi.fn(),
  resetAllCredits: vi.fn(),
}));

vi.mock('@/lib/api/adminAudit', () => apiMocks);
vi.mock('@/lib/api/credits', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/credits')>()),
  ...creditsMocks,
}));
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
  creditsMocks.fetchCreditsResetAll.mockReset().mockResolvedValue({ last_reset_at: null, reset_cards: { available: 0, next_expires_at: null } });
  creditsMocks.resetAllCredits.mockReset();
  adminNavigation.reset('/admin/users');
});

describe('AdminUsersPanel', () => {
  it('全员重置额度需要确认，成功后显示重置时间', async () => {
    creditsMocks.resetAllCredits.mockResolvedValue({ last_reset_at: '2026-10-26T07:00:00+00:00' });
    renderAdmin(<AdminUsersPanel />);
    await screen.findByText('@alpha');
    expect(screen.queryByTestId('credits-reset-all-at')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '全员重置额度' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(creditsMocks.resetAllCredits).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '全员重置额度' }));
    fireEvent.click(screen.getByRole('button', { name: '确认重置' }));

    await waitFor(() => expect(creditsMocks.resetAllCredits).toHaveBeenCalledTimes(1));
    expect((await screen.findByTestId('credits-reset-all-at')).textContent).toContain('2026/10/26 15:00:00');
  });

  it('额度弹窗展示用量与流水，二次确认后重置并刷新', async () => {
    const window = (used: number, limit: number) => ({ limit, used, remaining: limit - used, resets_at: '2026-10-26T16:00:00+00:00' });
    const credits = (used: number) => ({
      summary: { tier: 'user', unlimited: false, daily: window(used, 3000), weekly: window(used, 10000), last_reset_at: null, reset_cards: { available: 0, next_expires_at: null } },
      items: [{
        id: 'e1', kind: 'run', run_id: 'r1', conversation_id: 'c1', message_id: 'm1', model_id: 'deepseek-chat',
        run_status: 'completed', charged: true, credits: 120, occurred_at: '2026-10-26T05:00:00+00:00', breakdown: null,
      }],
      next_before: null,
    });
    creditsMocks.fetchAdminUserCredits.mockReset().mockResolvedValueOnce(credits(3000)).mockResolvedValueOnce(credits(0));
    creditsMocks.resetAdminUserCredits.mockReset().mockResolvedValue({});
    renderAdmin(<AdminUsersPanel />);
    await screen.findByText('@alpha');

    fireEvent.click(screen.getByRole('button', { name: '查看用户额度 user-alpha' }));

    const dialog = await screen.findByTestId('admin-user-credits');
    await waitFor(() => expect(dialog.textContent).toContain('3,000 / 3,000'));
    expect(creditsMocks.fetchAdminUserCredits).toHaveBeenCalledWith('user-alpha', expect.any(AbortSignal));
    expect(dialog.textContent).toContain('deepseek-chat');

    fireEvent.click(screen.getByRole('button', { name: '重置额度' }));
    expect(creditsMocks.resetAdminUserCredits).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认重置' }));

    await waitFor(() => expect(creditsMocks.resetAdminUserCredits).toHaveBeenCalledWith('user-alpha'));
    await waitFor(() => expect(dialog.textContent).toContain('0 / 3,000'));
  });

  it('额度弹窗二次确认后发放一张重置卡，刷新后显示可用张数与到期时间', async () => {
    const window = { limit: 3000, used: 3000, remaining: 0, resets_at: '2026-10-26T16:00:00+00:00' };
    const credits = (available: number) => ({
      summary: {
        tier: 'user', unlimited: false, daily: window, weekly: { ...window, limit: 10000, remaining: 7000 }, last_reset_at: null,
        reset_cards: { available, next_expires_at: available ? '2026-11-25T07:00:00+00:00' : null },
      },
      items: [{
        id: 'e2', kind: 'reset', run_id: null, conversation_id: null, message_id: null, model_id: null, run_status: null,
        charged: false, credits: 0, occurred_at: '2026-10-26T04:00:00+00:00', breakdown: null, source: 'reset_card',
      }],
      next_before: null,
    });
    creditsMocks.fetchAdminUserCredits.mockReset().mockResolvedValueOnce(credits(0)).mockResolvedValueOnce(credits(1));
    creditsMocks.grantAdminResetCards.mockReset().mockResolvedValue({});
    renderAdmin(<AdminUsersPanel />);
    await screen.findByText('@alpha');
    fireEvent.click(screen.getByRole('button', { name: '查看用户额度 user-alpha' }));

    const dialog = await screen.findByTestId('admin-user-credits');
    await waitFor(() => expect(dialog.textContent).toContain('0 张可用'));
    expect(dialog.textContent).toContain('使用重置卡');

    fireEvent.click(screen.getByRole('button', { name: '发放重置卡' }));
    expect(creditsMocks.grantAdminResetCards).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认发放' }));

    await waitFor(() => expect(creditsMocks.grantAdminResetCards).toHaveBeenCalledWith('user-alpha', 1));
    await waitFor(() => expect(dialog.textContent).toContain('1 张可用'));
    expect(dialog.textContent).toContain('到期');
  });

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
