'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { getAdminAuditEvents } from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { useAdminListParams } from '@/hooks/useAdminListParams';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import type { AdminAuditEventRecord } from '@/types/adminAudit';
import { AdminEmpty, AdminError, AdminFilterActions, AdminFilterField, AdminLoading, AdminPagination, AdminPanelHeader, formatAdminDate } from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';
import {
  adminAuditActionKind,
  adminAuditActionLabel,
  adminAuditResourceLabel,
  formatAdminAuditAdmin,
  formatAdminAuditMetadata,
  formatAdminAuditTargetUser,
} from '@/lib/admin/adminAuditPresentation';
import { useAdminAccess } from './AdminAccessContext';

const AUDIT_FILTER_KEYS = ['action', 'admin_user_id', 'target_user_id'] as const;

export default function AdminAuditEventsPanel() {
  const { revoke } = useAdminAccess();
  const { values, page, setFilters, setPage } = useAdminListParams(AUDIT_FILTER_KEYS);
  const [draft, setDraft] = useState(values);
  useEffect(() => setDraft(values), [values]);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const detailTriggerRef = useRef<HTMLButtonElement | null>(null);
  const loader = useCallback((signal: AbortSignal) => getAdminAuditEvents({ page, page_size: 25, ...values }, signal), [page, values]);
  const resource = useAdminAuditResource(loader, revoke);
  const selectedEvent = resource.data?.items.find(event => event.id === selectedEventId) ?? null;

  const applyFilters = (event: React.FormEvent) => {
    event.preventDefault();
    setSelectedEventId(null);
    setFilters(draft);
  };

  const resetFilters = () => {
    setSelectedEventId(null);
    setFilters({});
  };

  return (
    <section className={styles.listPage}>
      <AdminPanelHeader title="操作审计" description="查看管理员读取用户、对话等敏感数据，以及修改模型与 MCP 配置的留痕。" action={<Button variant="outline" size="sm" onClick={resource.reload}><RefreshCw />刷新</Button>} />
      <form className={`${styles.filters} ${styles.auditFilters}`} onSubmit={applyFilters}>
        <AdminFilterField label="动作"><Input aria-label="审计动作" placeholder="例如 admin.audit.conversation.view" value={draft.action} onChange={event => setDraft(current => ({ ...current, action: event.target.value }))} /></AdminFilterField>
        <AdminFilterField label="管理员用户 ID"><Input aria-label="管理员用户 ID" placeholder="管理员用户 ID" value={draft.admin_user_id} onChange={event => setDraft(current => ({ ...current, admin_user_id: event.target.value }))} /></AdminFilterField>
        <AdminFilterField label="目标用户 ID"><Input aria-label="目标用户 ID" placeholder="目标用户 ID" value={draft.target_user_id} onChange={event => setDraft(current => ({ ...current, target_user_id: event.target.value }))} /></AdminFilterField>
        <AdminFilterActions className={styles.filterActions} submitLabel="筛选" submitIcon={<Search />} onReset={resetFilters} />
      </form>
      {resource.loading ? <AdminLoading /> : resource.error ? <AdminError message={resource.error} onRetry={resource.reload} /> : null}
      {resource.data && resource.data.items.length === 0 ? <AdminEmpty>暂无操作审计记录</AdminEmpty> : null}
      {resource.data && resource.data.items.length > 0 ? <><div className={styles.tableFrame} role="region" aria-label="操作审计列表" tabIndex={0}><table className={`${styles.table} min-w-[860px]`}><caption className="sr-only">管理员操作审计记录</caption><colgroup><col style={{ width: '22%' }} /><col style={{ width: '16%' }} /><col style={{ width: '28%' }} /><col style={{ width: '22%' }} /><col style={{ width: '12%' }} /></colgroup><thead><tr><th scope="col">时间</th><th scope="col">管理员</th><th scope="col">操作内容</th><th scope="col">访问对象</th><th scope="col" className={styles.actions}>详情</th></tr></thead><tbody>{resource.data.items.map(event => {
        const admin = formatAdminAuditAdmin(event.admin_snapshot);
        const targetUser = formatAdminAuditTargetUser(event.target_user, event.target_user_id);
        return (
          <tr key={event.id}>
            <td className="whitespace-nowrap">{formatAdminDate(event.created_at)}</td>
            <td aria-label={`审计管理员 ${event.id}`}><div className="font-medium">{admin.primary}</div></td>
            <td aria-label={`审计操作 ${event.id}`}><AdminAuditActionCell action={event.action} /></td>
            <td aria-label={`审计对象 ${event.id}`}>
              <div className="font-medium">{adminAuditResourceLabel(event.resource_type)}</div>
              {targetUser ? <div className="mt-1 text-xs text-muted-foreground">{targetUser.primary}</div> : null}
            </td>
            <td className={styles.actions}>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`查看审计详情 ${event.id}`}
                onClick={clickEvent => {
                  detailTriggerRef.current = clickEvent.currentTarget;
                  setSelectedEventId(event.id);
                }}
              >查看详情</Button>
            </td>
          </tr>
        );
      })}</tbody></table></div><AdminPagination page={resource.data} onPageChange={nextPage => { setSelectedEventId(null); setPage(nextPage); }} /></> : null}
      <Dialog open={Boolean(selectedEvent)} onOpenChange={open => { if (!open) setSelectedEventId(null); }}>
        <DialogContent
          className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
          closeLabel="关闭审计详情"
          onCloseAutoFocus={closeEvent => {
            closeEvent.preventDefault();
            detailTriggerRef.current?.focus();
            detailTriggerRef.current = null;
          }}
        >
          <DialogHeader>
            <DialogTitle>审计事件详情</DialogTitle>
            <DialogDescription>查看本次管理员操作的完整标识与安全摘要。</DialogDescription>
          </DialogHeader>
          {selectedEvent ? <AdminAuditEventDetails event={selectedEvent} /> : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}

const ACTION_KIND_LABELS = { view: '查看', modify: '修改' } as const;

function AdminAuditActionCell({ action }: { action: string }) {
  const kind = adminAuditActionKind(action);
  return (
    <div className="flex items-center gap-2">
      {kind ? (
        <span
          data-kind={kind}
          className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${kind === 'modify' ? 'bg-amber-500/15 text-amber-700 dark:text-amber-300' : 'bg-muted text-muted-foreground'}`}
        >
          {ACTION_KIND_LABELS[kind]}
        </span>
      ) : null}
      <span>{adminAuditActionLabel(action)}</span>
    </div>
  );
}

function AdminAuditEventDetails({ event }: { event: AdminAuditEventRecord }) {
  const reason = event.reason?.trim();
  const metadata = formatAdminAuditMetadata(event.metadata);
  const targetUser = formatAdminAuditTargetUser(event.target_user, event.target_user_id);
  return (
    <dl aria-label={`审计详情 ${event.id}`} className="grid gap-2 rounded-lg border border-border bg-card p-3 text-xs">
      <AuditDetail label="审计事件 ID" value={event.id || '未记录'} />
      <AuditDetail label="管理员 ID" value={event.admin_user_id || '未记录'} />
      <AuditDetail label="目标用户 ID（审计关联）" value={event.target_user_id || '未记录'} />
      {targetUser ? <AuditDetail label="目标用户当前身份" value={targetUser.detail} /> : null}
      <AuditDetail label="资源 ID" value={event.resource_id || '未记录'} />
      <AuditDetail label="Request ID" value={event.request_id || '未记录'} />
      <AuditDetail label="原始操作" value={event.action || '未记录'} />
      <AuditDetail label="原始资源" value={event.resource_type || '未记录'} />
      {reason ? <AuditDetail label="理由" value={reason} /> : null}
      {metadata.length > 0 ? <div><dt className="text-muted-foreground">附加摘要</dt><dd className="mt-1 grid gap-1 rounded bg-muted/40 p-2">{metadata.map(item => <div key={`${item.label}-${item.value}`}><span>{item.label}：</span><span className="break-all">{item.value}</span></div>)}</dd></div> : null}
    </dl>
  );
}

function AuditDetail({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="break-all font-mono">{value}</dd></div>;
}
