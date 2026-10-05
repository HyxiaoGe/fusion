'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { getAdminUser, getAdminUsers } from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type { AdminUserDetail } from '@/types/adminAudit';
import { isAdminAccessError } from '@/lib/admin/adminAccess';
import AdminUserIdentity from './AdminUserIdentity';
import {
  AdminEmpty, AdminError, AdminFilterActions, AdminFilterField, AdminLoading, AdminPagination, AdminPanelHeader, formatAdminDate, formatNumber,
} from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

interface AdminUsersPanelProps {
  active?: boolean;
  onForbidden: () => void;
  selectedUserId: string | null;
  onOpen: (userId: string) => void;
  onClose: () => void;
  onViewConversations: (userId: string) => void;
}

export default function AdminUsersPanel({
  active = true, onForbidden, selectedUserId, onOpen, onClose, onViewConversations,
}: AdminUsersPanelProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState('');
  const [query, setQuery] = useState('');
  const [adminFilter, setAdminFilter] = useState<'' | 'true' | 'false'>('');
  const [createdFrom, setCreatedFrom] = useState('');
  const [createdTo, setCreatedTo] = useState('');
  const [selectedUser, setSelectedUser] = useState<AdminUserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const detailControllerRef = useRef<AbortController | null>(null);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const loader = useCallback((signal: AbortSignal) => getAdminUsers({
    page,
    page_size: 25,
    q: query,
    is_superuser: adminFilter === '' ? undefined : adminFilter === 'true',
    created_from: createdFrom,
    created_to: createdTo,
  }, signal), [adminFilter, createdFrom, createdTo, page, query]);
  const resource = useAdminAuditResource(loader, onForbidden);

  const clearLoadedUserDetail = useCallback(() => {
    detailControllerRef.current?.abort();
    detailControllerRef.current = null;
    setSelectedUser(null);
    setDetailError(null);
    setDetailLoading(false);
  }, []);

  const refreshUsers = () => {
    if (selectedUserId) onClose();
    resource.reload();
  };

  useEffect(() => () => detailControllerRef.current?.abort(), []);

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    setPage(1);
    setQuery(searchDraft.trim());
  };

  const resetFilters = () => {
    setPage(1);
    setSearchDraft('');
    setQuery('');
    setAdminFilter('');
    setCreatedFrom('');
    setCreatedTo('');
  };

  const loadUser = useCallback(async (userId: string) => {
    detailControllerRef.current?.abort();
    const controller = new AbortController();
    detailControllerRef.current = controller;
    setSelectedUser(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const user = await getAdminUser(userId, controller.signal);
      if (controller.signal.aborted) return;
      setSelectedUser(user);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (isAdminAccessError(error)) onForbidden();
      else setDetailError(error instanceof Error ? error.message : '用户详情读取失败');
    } finally {
      if (!controller.signal.aborted) setDetailLoading(false);
    }
  }, [onForbidden]);

  useEffect(() => {
    clearLoadedUserDetail();
    if (selectedUserId) void loadUser(selectedUserId);
    return () => detailControllerRef.current?.abort();
  }, [clearLoadedUserDetail, loadUser, selectedUserId]);

  const viewUserConversations = () => {
    const userId = selectedUser?.id ?? selectedUserId;
    if (!userId) return;
    onViewConversations(userId);
  };

  return (
    <section>
      <AdminPanelHeader
        title="用户审计"
        description="检索全部用户，查看活跃度、对话统计与 token 汇总。"
        action={<Button variant="outline" size="sm" onClick={refreshUsers} aria-label="刷新用户列表"><RefreshCw />刷新</Button>}
      />
      <form onSubmit={submitSearch} className={`${styles.filters} ${styles.usersFilters}`}>
        <AdminFilterField label={t('admin.filters.userSearch')}><Input ref={searchInputRef} aria-label={t('admin.filters.userSearch')} value={searchDraft} onChange={event => setSearchDraft(event.target.value)} placeholder="用户 ID、用户名、昵称或邮箱" /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.role')}><select aria-label={t('admin.filters.role')} className="px-3 text-sm" value={adminFilter} onChange={event => setAdminFilter(event.target.value as '' | 'true' | 'false')}><option value="">权限不限</option><option value="true">仅管理员</option><option value="false">仅普通用户</option></select></AdminFilterField>
        <AdminFilterField label={t('admin.filters.registeredFrom')}><Input aria-label={t('admin.filters.registeredFrom')} type="date" value={createdFrom} onChange={event => setCreatedFrom(event.target.value)} /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.registeredTo')}><Input aria-label={t('admin.filters.registeredTo')} type="date" value={createdTo} onChange={event => setCreatedTo(event.target.value)} /></AdminFilterField>
        <AdminFilterActions className={styles.filterActions} submitLabel="搜索" submitIcon={<Search />} onReset={resetFilters} />
      </form>

      {resource.loading ? <AdminLoading /> : resource.error ? <AdminError message={resource.error} onRetry={refreshUsers} retryLabel="刷新用户列表" /> : null}
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
                    <td className={styles.actions}><Button variant="ghost" size="sm" aria-label={`查看用户详情 ${user.id}`} onClick={event => { detailTriggerRef.current = event.currentTarget; onOpen(user.id); }}>查看详情</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <AdminPagination page={resource.data} onPageChange={setPage} />
        </>
      ) : null}

      <Dialog open={active && Boolean(selectedUserId)} onOpenChange={open => { if (!open && active) onClose(); }}>
        <DialogContent
          className={`max-h-[85vh] overflow-y-auto sm:max-w-2xl ${styles.dialog} ${styles.controls}`}
          onCloseAutoFocus={event => {
            event.preventDefault();
            (detailTriggerRef.current ?? searchInputRef.current)?.focus();
            detailTriggerRef.current = null;
          }}
        >
          <DialogHeader>
            <DialogTitle>用户详情</DialogTitle>
            <DialogDescription>查看用户身份、注册信息和管理员可见配置。</DialogDescription>
          </DialogHeader>
          {detailLoading ? <div role="status" className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">正在读取用户详情…</div> : null}
          {detailError ? (
            <AdminError
              message={detailError}
              onRetry={() => { if (selectedUserId) void loadUser(selectedUserId); }}
              retryLabel="重试用户详情"
            />
          ) : null}
          {selectedUser ? (
            <aside aria-label={`用户详情 ${selectedUser.id}`} className="rounded-xl border border-border bg-card p-4">
              <AdminUserIdentity user={selectedUser} />
              <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-xs text-muted-foreground">完整邮箱</dt><dd>{selectedUser.email || '未采集'}</dd></div><div><dt className="text-xs text-muted-foreground">注册时间</dt><dd>{formatAdminDate(selectedUser.created_at)}</dd></div></dl>
              {selectedUser.system_prompt?.trim() ? (
                <details className="mt-4 rounded-md border border-border/60 p-3"><summary className="cursor-pointer text-sm font-medium">自定义提示词</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs text-muted-foreground">{selectedUser.system_prompt}</pre></details>
              ) : null}
            </aside>
          ) : null}
          {selectedUser ? (
            <DialogFooter>
              <Button className={styles.primaryAction} onClick={viewUserConversations}>查看该用户的对话</Button>
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}
