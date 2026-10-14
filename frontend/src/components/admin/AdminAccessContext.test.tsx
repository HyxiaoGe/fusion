import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adminNavigation } from '@/test/adminNavigation';

vi.mock('next/navigation', async () => (await import('@/test/adminNavigation')).nextNavigationMock);

import { AdminAccessProvider, useAdminAccess } from './AdminAccessContext';

function Probe() {
  const { revoke, goBack } = useAdminAccess();
  return (
    <>
      <div>敏感内容</div>
      <button type="button" onClick={() => goBack('/admin/users')}>返回</button>
      <button type="button" onClick={revoke}>撤销</button>
    </>
  );
}

describe('AdminAccessProvider', () => {
  beforeEach(() => adminNavigation.reset('/admin/users/user-1'));

  it('深链进入时返回替换到父级列表', () => {
    render(<AdminAccessProvider><Probe /></AdminAccessProvider>);
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(adminNavigation.back).not.toHaveBeenCalled();
    expect(adminNavigation.history).toEqual(['/admin/users']);
  });

  it('站内导航后返回走浏览器历史', () => {
    adminNavigation.reset('/admin/users');
    render(<AdminAccessProvider><Probe /></AdminAccessProvider>);
    act(() => adminNavigation.push('/admin/users/user-1'));
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(adminNavigation.back).toHaveBeenCalledTimes(1);
    expect(adminNavigation.url).toBe('/admin/users');
  });

  it('撤销后卸载全部内容并移除 URL 中的资源 ID', () => {
    render(<AdminAccessProvider><Probe /></AdminAccessProvider>);
    fireEvent.click(screen.getByRole('button', { name: '撤销' }));
    expect(screen.getByText('管理员权限已失效')).toBeInTheDocument();
    expect(screen.queryByText('敏感内容')).toBeNull();
    expect(adminNavigation.url).toBe('/admin/users');
  });
});
