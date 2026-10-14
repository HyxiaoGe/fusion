'use client';

import { useCallback, useState } from 'react';
import {
  getAdminConversation,
  getAdminConversationAgentRuns,
  getAdminConversationFiles,
  getAdminConversationMessages,
  getAdminConversationToolCalls,
} from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { adminPaths } from '@/lib/admin/adminRoutes';
import { useAdminAccess } from './AdminAccessContext';
import AdminExecutionInspector from './AdminExecutionInspector';
import AdminMessageCard from './AdminMessageCard';
import AdminUserIdentity from './AdminUserIdentity';
import {
  AdminBackButton, AdminEmpty, AdminError, AdminLoading, AdminPagination, formatAdminDate, formatNumber,
} from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

export default function AdminConversationDetailView({ conversationId }: { conversationId: string }) {
  const { revoke } = useAdminAccess();
  const [messagePage, setMessagePage] = useState(1);
  const [runPage, setRunPage] = useState(1);
  const [toolPage, setToolPage] = useState(1);
  const [filePage, setFilePage] = useState(1);
  const detailLoader = useCallback((signal: AbortSignal) => getAdminConversation(conversationId, signal), [conversationId]);
  const messageLoader = useCallback((signal: AbortSignal) => getAdminConversationMessages(conversationId, { page: messagePage, page_size: 25 }, signal), [conversationId, messagePage]);
  const toolLoader = useCallback((signal: AbortSignal) => getAdminConversationToolCalls(conversationId, { page: toolPage, page_size: 25 }, signal), [conversationId, toolPage]);
  const runLoader = useCallback((signal: AbortSignal) => getAdminConversationAgentRuns(conversationId, { page: runPage, page_size: 25 }, signal), [conversationId, runPage]);
  const fileLoader = useCallback((signal: AbortSignal) => getAdminConversationFiles(conversationId, { page: filePage, page_size: 25 }, signal), [conversationId, filePage]);
  const detail = useAdminAuditResource(detailLoader, revoke);
  const messages = useAdminAuditResource(messageLoader, revoke);
  const tools = useAdminAuditResource(toolLoader, revoke);
  const runs = useAdminAuditResource(runLoader, revoke);
  const files = useAdminAuditResource(fileLoader, revoke);

  return (
    <section aria-label={`对话详情 ${conversationId}`}>
      <AdminBackButton label="返回对话列表" fallbackHref={adminPaths.conversations()} />
      {detail.loading ? <AdminLoading /> : detail.error ? <AdminError message={detail.error} onRetry={detail.reload} /> : detail.data ? (
        <header className={styles.detailSummary}>
          <h1 className={styles.detailTitle}>{detail.data.title || '未命名对话'}</h1>
          <div className="mt-4 grid min-w-0 gap-4 md:grid-cols-2">
            <dl className={styles.detailMetadata}>
              <div><dt>对话 ID</dt><dd><code>{detail.data.id}</code></dd></div>
              <div><dt>模型</dt><dd>{detail.data.model_id || '—'}</dd></div>
              <div><dt>更新时间</dt><dd>{formatAdminDate(detail.data.updated_at)}</dd></div>
            </dl>
            <div className={styles.detailIdentity}><AdminUserIdentity user={detail.data.user} /></div>
          </div>
        </header>
      ) : null}

      <div className={styles.conversationDetailGrid}>
        <section className={styles.detailSection} aria-label="消息">
          <header className={styles.detailSectionHeader}>
            <h2>消息</h2>
            {messages.data ? <span>{formatNumber(messages.data.total)}</span> : null}
          </header>
          {messages.loading ? <AdminLoading /> : messages.error ? <AdminError message={messages.error} onRetry={messages.reload} /> : messages.data?.items.length ? (
            <><div className={styles.messageList}>{messages.data.items.map(message => <AdminMessageCard key={message.id} message={message} />)}</div><AdminPagination page={messages.data} onPageChange={setMessagePage} /></>
          ) : <AdminEmpty>没有已持久化消息</AdminEmpty>}
        </section>

        <div className={styles.detailSidebar}>
          <section className={styles.detailSection} aria-label="Agent 运行记录">
            <header className={styles.detailSectionHeader}>
              <h2>Agent 运行</h2>
              {runs.data ? <span>{formatNumber(runs.data.total)}</span> : null}
            </header>
            {runs.loading ? <AdminLoading /> : runs.error ? <AdminError message={runs.error} onRetry={runs.reload} /> : runs.data?.items.length ? (
              <><AdminExecutionInspector runs={runs.data.items} toolCalls={[]} /><AdminPagination page={runs.data} onPageChange={setRunPage} /></>
            ) : <AdminEmpty>没有 Agent 运行记录</AdminEmpty>}
          </section>

          <section className={styles.detailSection} aria-label="工具调用记录">
            <header className={styles.detailSectionHeader}>
              <h2>工具调用</h2>
              {tools.data ? <span>{formatNumber(tools.data.total)}</span> : null}
            </header>
            {tools.loading ? <AdminLoading /> : tools.error ? <AdminError message={tools.error} onRetry={tools.reload} /> : tools.data?.items.length ? (
              <><AdminExecutionInspector runs={[]} toolCalls={tools.data.items} /><AdminPagination page={tools.data} onPageChange={setToolPage} /></>
            ) : <AdminEmpty>没有工具调用记录</AdminEmpty>}
          </section>

          <section className={styles.detailSection} aria-label="文件元数据">
            <header className={styles.detailSectionHeader}>
              <h2>文件元数据</h2>
              {files.data ? <span>{formatNumber(files.data.total)}</span> : null}
            </header>
            {files.loading ? <AdminLoading /> : files.error ? <AdminError message={files.error} onRetry={files.reload} /> : files.data?.items.length ? (
              <>
                <div className="space-y-2">
                  {files.data.items.map(file => (
                    <div key={file.id} className={styles.fileMetadata} data-layout="stack">
                      <div className={styles.fileName}>{file.original_filename}</div>
                      <div className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span className="break-all">{file.mimetype || '未知类型'}</span>
                        <span>{formatFileSize(file.size)}</span>
                        <span>{file.status || '状态未知'}</span>
                        {file.width && file.height ? <span>{file.width}×{file.height}</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
                <AdminPagination page={files.data} onPageChange={setFilePage} />
              </>
            ) : <AdminEmpty>没有关联文件元数据</AdminEmpty>}
          </section>
        </div>
      </div>
    </section>
  );
}

function formatFileSize(value: number | null): string {
  if (value === null) return '大小未知';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
