import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adminNavigation } from '@/test/adminNavigation';

vi.mock('next/navigation', async () => (await import('@/test/adminNavigation')).nextNavigationMock);
vi.mock('next/link', () => ({
  default: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props} data-next-client-link="true">{children}</a>
  ),
}));

import AdminShell from './AdminShell';

describe('AdminShell', () => {
  beforeEach(() => adminNavigation.reset('/admin/users'));

  it('通过普通链接硬导航返回聊天，使 document CSP 重新加载', () => {
    render(<AdminShell><div>审计内容</div></AdminShell>);

    const link = screen.getByRole('link', { name: '返回聊天' });
    expect(link).toHaveAttribute('href', '/chat/new');
    expect(link).not.toHaveAttribute('data-next-client-link');
    expect(adminNavigation.push).not.toHaveBeenCalled();
  });

  it('左侧分组导航指向各管理页面，详情页仍高亮所属列表', () => {
    adminNavigation.reset('/admin/conversations/conv-1');
    render(<AdminShell><div>审计内容</div></AdminShell>);

    const nav = screen.getByRole('navigation', { name: '管理中心导航' });
    expect(nav).toHaveTextContent('审计');
    expect(nav).toHaveTextContent('模型');
    expect(nav).toHaveTextContent('服务');
    const hrefs = Array.from(nav.querySelectorAll('a')).map(link => link.getAttribute('href'));
    expect(hrefs).toEqual([
      '/admin/users', '/admin/conversations', '/admin/audit-log',
      '/admin/models', '/admin/model-visibility', '/admin/quality/itinerary',
      '/admin/mcp', '/admin/usage',
    ]);
    expect(screen.getByRole('link', { name: '对话' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: '用户' })).not.toHaveAttribute('aria-current');
  });
});
