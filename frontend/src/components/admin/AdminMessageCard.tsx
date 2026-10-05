'use client';

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, CheckCircle, ChevronDown, FileText, User } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { AdminJsonValue, AdminKnownContentBlock, AdminMessageRecord } from '@/types/adminAudit';
import AdminSafeMarkdown from './AdminSafeMarkdown';
import { formatAdminDate } from './AdminPanelPrimitives';
import styles from './AdminSurface.module.css';

const KNOWN_BLOCK_TYPES = new Set(['text', 'thinking', 'file', 'search', 'url_read']);

export default function AdminMessageCard({ message }: { message: AdminMessageRecord }) {
  const { t } = useTranslation();
  const [reasoningVisible, setReasoningVisible] = useState(false);
  const text = useMemo(
    () => message.content.filter(block => block.type === 'text').map(block => block.text ?? '').join(''),
    [message.content],
  );
  const thinking = useMemo(
    () => message.content.filter(block => block.type === 'thinking').map(block => block.thinking ?? '').join('\n'),
    [message.content],
  );
  const metadataBlocks = message.content.filter(block => block.type !== 'text' && block.type !== 'thinking');
  const isUser = message.role === 'user';

  return (
    <article className={styles.messageCard} data-role={message.role} data-testid={`admin-message-${message.id}`}>
      <header className={styles.messageHeader}>
        <div className={styles.messageIdentity}>
          {isUser ? <User className="h-4 w-4" aria-hidden="true" /> : <Bot className="h-4 w-4" aria-hidden="true" />}
          <h3 className="text-sm font-medium">{isUser ? '用户' : '助手'}</h3>
          {message.model_id ? <Badge variant="outline" className="max-w-full whitespace-normal break-all">{message.model_id}</Badge> : null}
        </div>
        <div className="text-xs text-muted-foreground">
          {formatAdminDate(message.created_at)}
        </div>
      </header>

      {thinking ? (
        <div className="mb-3 overflow-hidden rounded-lg border border-border/50">
          <button
            type="button"
            onClick={() => setReasoningVisible(current => !current)}
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            aria-expanded={reasoningVisible}
          >
            <span className="flex items-center gap-2"><CheckCircle className="h-3.5 w-3.5" />已深度思考</span>
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${reasoningVisible ? 'rotate-180' : ''}`} />
          </button>
          {reasoningVisible ? (
            <div className="border-t border-border/40 px-3 py-2 text-xs text-muted-foreground">
              <AdminSafeMarkdown content={thinking} className={styles.messageBody} />
            </div>
          ) : null}
        </div>
      ) : null}

      {text ? <AdminSafeMarkdown content={text} className={styles.messageBody} /> : null}

      {metadataBlocks.length > 0 ? (
        <section className={styles.messageMetadata} aria-label={t('admin.details.additionalContent')}>
          <h4 className="text-xs font-medium text-muted-foreground">{t('admin.details.additionalContent')}</h4>
          {metadataBlocks.map((block, index) => (
            <MetadataBlock key={block.id ?? `${block.type}-${index}`} block={block} />
          ))}
        </section>
      ) : null}

      {message.usage ? (
        <footer className={styles.messageFooter}>
          输入 {message.usage.input_tokens} · 输出 {message.usage.output_tokens} tokens
        </footer>
      ) : null}
    </article>
  );
}

function MetadataBlock({ block }: { block: AdminKnownContentBlock }) {
  if (block.type === 'file') {
    return (
      <div className={styles.fileMetadata}>
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className={styles.fileName}>{block.filename || '未命名文件'}</span>
        <span className="min-w-0 break-all text-xs text-muted-foreground">{block.mime_type || '未知类型'}</span>
      </div>
    );
  }

  if (block.type === 'search') {
    return <SafeProjection title="联网搜索" value={block.query || '内容已隐藏'} />;
  }

  if (block.type === 'url_read') {
    return <SafeProjection title="网页读取" value={block.title || block.url || '内容已隐藏'} />;
  }

  if (!KNOWN_BLOCK_TYPES.has(block.type)) {
    return <SafeProjection title={`未知内容块：${block.type}`} value="内容已隐藏" />;
  }

  return null;
}

function SafeProjection({ title, value }: { title: string; value: AdminJsonValue | undefined }) {
  return (
    <details className={styles.rawDetails}>
      <summary className="cursor-pointer text-xs font-medium">{title}</summary>
      <pre className={styles.rawContent}>
        {formatSafeJson(value)}
      </pre>
    </details>
  );
}

function formatSafeJson(value: AdminJsonValue | undefined): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value ?? null, null, 2);
}
