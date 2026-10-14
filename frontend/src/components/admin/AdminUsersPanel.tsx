'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { getAdminUsers } from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { useAdminListParams } from '@/hooks/useAdminListParams';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { adminPaths } from '@/lib/admin/adminRoutes';
import { useAdminAccess } from './AdminAccessContext';
import AdminUserIdentity from './AdminUserIdentity';
import {
  AdminEmpty, AdminError, AdminFilterActions, AdminFilterField, AdminLoading, AdminPagination, AdminPanelHeader, formatAdminDate, formatNumber,
} from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

const USER_FILTER_KEYS = ['q', 'is_superuser', 'created_from', 'created_to'] as const;

export default function AdminUsersPanel() {
  const { t } = useTranslation();
  const { revoke } = useAdminAccess();
  const { values, page, setFilters, setPage } = useAdminListParams(USER_FILTER_KEYS);
  const [draft, setDraft] = useState(values);
  useEffect(() => setDraft(values), [values]);
  const loader = useCallback((signal: AbortSignal) => getAdminUsers({
    page,
    page_size: 25,
    q: values.q,
    is_superuser: values.is_superuser === 'true' ? true : values.is_superuser === 'false' ? false : undefined,
    created_from: values.created_from,
    created_to: values.created_to,
  }, signal), [page, values]);
  const resource = useAdminAuditResource(loader, revoke);

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    setFilters(draft);
  };

  const resetFilters = () => setFilters({});

  return (
    <section className={styles.listPage}>
      <AdminPanelHeader
        title="用户审计"
        description="检索全部用户，查看活跃度、对话统计与 token 汇总。"
        action={<Button variant="outline" size="sm" onClick={resource.reload} aria-label="刷新用户列表"><RefreshCw />刷新</Button>}
      />
      <form onSubmit={submitSearch} className={`${styles.filters} ${styles.usersFilters}`}>
        <AdminFilterField label={t('admin.filters.userSearch')}><Input aria-label={t('admin.filters.userSearch')} value={draft.q} onChange={event => setDraft(current => ({ ...current, q: event.target.value }))} placeholder="用户 ID、用户名、昵称或邮箱" /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.role')}><select aria-label={t('admin.filters.role')} className="px-3 text-sm" value={draft.is_superuser} onChange={event => setDraft(current => ({ ...current, is_superuser: event.target.value }))}><option value="">权限不限</option><option value="true">仅管理员</option><option value="false">仅普通用户</option></select></AdminFilterField>
        <AdminFilterField label={t('admin.filters.registeredFrom')}><Input aria-label={t('admin.filters.registeredFrom')} type="date" value={draft.created_from} onChange={event => setDraft(current => ({ ...current, created_from: event.target.value }))} /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.registeredTo')}><Input aria-label={t('admin.filters.registeredTo')} type="date" value={draft.created_to} onChange={event => setDraft(current => ({ ...current, created_to: event.target.value }))} /></AdminFilterField>
        <AdminFilterActions className={styles.filterActions} submitLabel="搜索" submitIcon={<Search />} onReset={resetFilters} />
      </form>

      {resource.loading ? <AdminLoading /> : resource.error ? <AdminError message={resource.error} onRetry={resource.reload} retryLabel="刷新用户列表" /> : null}
      {resource.data && resource.data.items.length === 0 ? <AdminEmpty>没有匹配的用户</AdminEmpty> : null}
      {resource.data && resource.data.items.length > 0 ? (
        <>
          <div className={styles.tableFrame} role="region" aria-label={t('admin.usersList')} tabIndex={0}>
            <table className={`${styles.table} min-w-[1100px]`}>
              <colgroup><col style={{ width: '25%' }} /><col style={{ width: '16%' }} /><col style={{ width: '19%' }} /><col style={{ width: '6%' }} /><col style={{ width: '6%' }} /><col style={{ width: '6%' }} /><col style={{ width: '11%' }} /><col style={{ width: '11%' }} /></colgroup>
              <thead><tr><th scope="col">用户</th><th scope="col">邮箱</th><th scope="col">活跃时间</th><th scope="col" className={styles.numeric}>对话</th><th scope="col" className={styles.numeric}>消息</th><th scope="col" className={styles.numeric}>工具</th><th scope="col" className={styles.numeric}>Token</th><th scope="col" className={styles.actions}>操作</th></tr></thead>
              <tbody>
                {resource.data.items.map(user => (
                  <tr key={user.id}>
                    <td><div className="flex min-w-0 flex-wrap items-start gap-2"><div className="min-w-0 flex-1"><AdminUserIdentity user={user} showEmail={false} compact /></div>{user.is_superuser ? <Badge variant="outline" className={styles.status} data-tone="info"><ShieldCheck className="mr-1 h-3 w-3" />管理员</Badge> : null}</div></td>
                    <td>{user.email_masked || '—'}</td><td className="text-xs text-muted-foreground">{formatAdminDate(user.last_active_at)}</td>
                    <td className={styles.numeric}>{formatNumber(user.conversation_count)}</td><td className={styles.numeric}>{formatNumber(user.message_count)}</td><td className={styles.numeric}>{formatNumber(user.tool_call_count)}</td>
                    <td className={styles.numeric}>{formatNumber(user.input_tokens + user.output_tokens)}</td>
                    <td className={styles.actions}><Button asChild variant="ghost" size="sm"><Link href={adminPaths.user(user.id)} aria-label={`查看用户详情 ${user.id}`}>查看详情</Link></Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <AdminPagination page={resource.data} onPageChange={setPage} />
        </>
      ) : null}

    </section>
  );
}
