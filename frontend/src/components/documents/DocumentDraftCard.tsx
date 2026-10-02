'use client';

import { memo, useDeferredValue, useEffect, useRef, useState } from 'react';
import { FileText, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getDocument } from '@/lib/api/documents';
import type { DocumentDraftState } from '@/types/agentRun';
import DocumentMarkdown from './DocumentMarkdown';
import styles from './DocumentCards.module.css';

interface DocumentDraftCardProps {
  draft: DocumentDraftState;
}

// 距底部小于该值视为“跟随最新内容”；用户上滚阅读时不抢滚动位置。
const STICK_TO_BOTTOM_PX = 48;
// document_id 按片段到达，只在拼成完整 UUID 后才查询，避免拿残缺 id 请求。
const DOCUMENT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 修订调用不带新标题时，用目标文档的现有标题，而不是显示“未命名”。 */
function useRevisionTitle(documentId: string, enabled: boolean): string {
  const [resolved, setResolved] = useState<{ documentId: string; title: string } | null>(null);

  useEffect(() => {
    if (!enabled || !DOCUMENT_ID_PATTERN.test(documentId)) return;
    const controller = new AbortController();
    getDocument(documentId, controller.signal)
      .then(detail => setResolved({ documentId, title: detail.title }))
      .catch(() => undefined);
    return () => controller.abort();
  }, [documentId, enabled]);

  return enabled && resolved?.documentId === documentId ? resolved.title : '';
}

/** 生成期的文档草稿：正文来自工具参数的实时片段，落库后由正式文档卡片替换。 */
function DocumentDraftCard({ draft }: DocumentDraftCardProps) {
  const { t, i18n } = useTranslation();
  const isRevision = draft.toolName === 'edit_document';
  const revisionTitle = useRevisionTitle(draft.documentId, isRevision && !draft.title);
  // 正文逐 token 增长，延迟渲染避免每个片段都同步重排整篇 Markdown。
  const previewContent = useDeferredValue(draft.content);
  const previewRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const element = previewRef.current;
    if (element && stickToBottomRef.current) element.scrollTop = element.scrollHeight;
  }, [previewContent]);

  const status = isRevision
    ? t('documents.draft.revising')
    : [
        t('documents.draft.writing'),
        t('documents.card.chars', { chars: draft.content.length.toLocaleString(i18n.language) }),
      ].join(' · ');

  return (
    <div className="mb-4 flex w-full max-w-6xl flex-col gap-2" data-testid="document-draft">
      <div className={styles.draftCard} aria-live="polite">
        <span className={styles.icon} aria-hidden="true">
          <FileText className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="block truncate text-sm font-medium text-foreground">
            {draft.title || revisionTitle || t('documents.draft.untitled')}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">{status}</span>
        </span>
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
      {!isRevision && previewContent ? (
        <div
          ref={previewRef}
          className={styles.draftPreview}
          role="region"
          aria-label={t('documents.draft.preview')}
          onScroll={event => {
            const element = event.currentTarget;
            stickToBottomRef.current =
              element.scrollHeight - element.scrollTop - element.clientHeight < STICK_TO_BOTTOM_PX;
          }}
        >
          <DocumentMarkdown content={previewContent} />
        </div>
      ) : null}
    </div>
  );
}

export default memo(DocumentDraftCard);
