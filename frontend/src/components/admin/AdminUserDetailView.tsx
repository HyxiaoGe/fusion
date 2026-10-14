'use client';

import { useCallback } from 'react';
import Link from 'next/link';
import { getAdminUser } from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { Button } from '@/components/ui/button';
import { adminPaths } from '@/lib/admin/adminRoutes';
import { useAdminAccess } from './AdminAccessContext';
import AdminUserIdentity from './AdminUserIdentity';
import { AdminBackButton, AdminError, AdminLoading, AdminPanelHeader, formatAdminDate } from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

export default function AdminUserDetailView({ userId }: { userId: string }) {
  const { revoke } = useAdminAccess();
  const loader = useCallback((signal: AbortSignal) => getAdminUser(userId, signal), [userId]);
  const resource = useAdminAuditResource(loader, revoke);
  const user = resource.data;

  return (
    <section>
      <AdminBackButton label="返回用户列表" fallbackHref={adminPaths.users} />
      <AdminPanelHeader title="用户详情" description="查看用户身份、注册信息和管理员可见配置。" />
      {resource.loading ? <AdminLoading /> : null}
      {resource.error ? <AdminError message={resource.error} onRetry={resource.reload} retryLabel="重试用户详情" /> : null}
      {user ? (
        <aside aria-label={`用户详情 ${user.id}`} className={styles.detailSummary}>
          <AdminUserIdentity user={user} />
          <dl className={`mt-4 ${styles.detailMetadata}`}><div><dt>完整邮箱</dt><dd>{user.email || '未采集'}</dd></div><div><dt>注册时间</dt><dd>{formatAdminDate(user.created_at)}</dd></div></dl>
          {user.system_prompt?.trim() ? (
            <details className={`mt-4 ${styles.rawDetails}`}><summary className="cursor-pointer text-sm font-medium">自定义提示词</summary><pre className={styles.rawContent}>{user.system_prompt}</pre></details>
          ) : null}
          <div className="mt-5">
            <Button asChild className={styles.primaryAction}><Link href={adminPaths.conversations({ userId: user.id })}>查看该用户的对话</Link></Button>
          </div>
        </aside>
      ) : null}
    </section>
  );
}
