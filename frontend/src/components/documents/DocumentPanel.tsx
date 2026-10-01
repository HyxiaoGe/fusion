'use client';

import { useEffect, useRef, useState } from 'react';
import { Download, FileCode2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { getDocument, getDocumentContent } from '@/lib/api/documents';
import {
  buildDocumentFilename,
  buildHtmlExport,
  buildMarkdownExport,
  downloadTextFile,
} from '@/lib/documents/documentExport';
import type { DocumentDetail, DocumentVersionContent } from '@/types/document';
import { useChatDetailOverlayRegistration } from '@/components/chat/ChatDetailOverlayContext';
import { ChatDetailOverlayPortal } from '@/components/chat/ChatDetailOverlayPortal';
import DocumentMarkdown from './DocumentMarkdown';
import DocumentSources from './DocumentSources';
import styles from './DocumentPanel.module.css';

interface DocumentPanelProps {
  documentId: string;
  initialVersion: number;
  isOpen: boolean;
  onClose: () => void;
}

type LoadState<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error' };

export default function DocumentPanel({ documentId, initialVersion, isOpen, onClose }: DocumentPanelProps) {
  const { t, i18n } = useTranslation();
  const [version, setVersion] = useState(initialVersion);
  const [detail, setDetail] = useState<LoadState<DocumentDetail>>({ status: 'loading' });
  const [content, setContent] = useState<LoadState<DocumentVersionContent>>({ status: 'loading' });
  const [reloadTick, setReloadTick] = useState(0);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  useChatDetailOverlayRegistration(isOpen);

  useEffect(() => {
    if (isOpen) setVersion(initialVersion);
  }, [initialVersion, isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    setDetail({ status: 'loading' });
    getDocument(documentId, controller.signal)
      .then(data => setDetail({ status: 'ready', data }))
      .catch(() => {
        if (!controller.signal.aborted) setDetail({ status: 'error' });
      });
    return () => controller.abort();
  }, [documentId, isOpen, reloadTick]);

  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    setContent({ status: 'loading' });
    getDocumentContent(documentId, version, controller.signal)
      .then(data => setContent({ status: 'ready', data }))
      .catch(() => {
        if (!controller.signal.aborted) setContent({ status: 'error' });
      });
    return () => controller.abort();
  }, [documentId, isOpen, version, reloadTick]);

  // 父组件在流式输出中频繁重渲染，onClose 引用会变；用 ref 避免反复抢焦点。
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previousFocus?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const ready = content.status === 'ready' ? content.data : null;
  const title = ready?.title ?? (detail.status === 'ready' ? detail.data.title : t('documents.panel.fallbackTitle'));
  const versions = detail.status === 'ready' ? detail.data.versions : [];

  const handleDownloadMarkdown = () => {
    if (!ready) return;
    downloadTextFile(
      buildDocumentFilename(ready.title, ready.version, 'md'),
      buildMarkdownExport(ready, t, i18n.language),
      'text/markdown',
    );
  };

  const handleDownloadHtml = async () => {
    if (!ready) return;
    const { renderToStaticMarkup } = await import('react-dom/server');
    const body = renderToStaticMarkup(
      <>
        <DocumentMarkdown content={ready.content} expandTabs />
        <DocumentSources sources={ready.sources} />
      </>,
    );
    downloadTextFile(
      buildDocumentFilename(ready.title, ready.version, 'html'),
      buildHtmlExport(ready.title, body, i18n.language),
      'text/html',
    );
  };

  return (
    <ChatDetailOverlayPortal>
      <button
        type="button"
        aria-label={t('documents.panel.closeBackdrop')}
        data-chat-detail-overlay-surface="true"
        className="fixed inset-0 z-40 cursor-default bg-black/20 p-0"
        onClick={onClose}
      />
      <aside
        data-testid="document-panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-chat-detail-overlay-surface="true"
        className={cn('fixed inset-y-0 right-0 z-50 flex w-[min(880px,100vw)] flex-col', styles.panel)}
      >
        <header className={cn('flex items-center justify-between gap-3 px-5 py-3', styles.header)}>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-medium">{title}</h3>
            {ready?.change_summary ? (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{ready.change_summary}</p>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {versions.length > 1 ? (
              <select
                aria-label={t('documents.panel.versionLabel')}
                className={styles.versionSelect}
                value={version}
                onChange={event => setVersion(Number(event.target.value))}
              >
                {[...versions].reverse().map(item => (
                  <option key={item.version} value={item.version}>
                    v{item.version}{item.change_summary ? ` · ${item.change_summary}` : ''}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              type="button"
              className={styles.actionButton}
              onClick={handleDownloadMarkdown}
              disabled={!ready}
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {t('documents.panel.downloadMarkdown')}
            </button>
            <button
              type="button"
              className={styles.actionButton}
              onClick={() => { void handleDownloadHtml(); }}
              disabled={!ready}
            >
              <FileCode2 className="h-3.5 w-3.5" aria-hidden="true" />
              {t('documents.panel.downloadHtml')}
            </button>
            <button
              ref={closeButtonRef}
              type="button"
              aria-label={t('documents.panel.close')}
              onClick={onClose}
              className={styles.iconButton}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className={cn('min-h-0 flex-1 overflow-y-auto px-8 py-6', styles.body)}>
          {content.status === 'loading' ? (
            <p className="text-sm text-muted-foreground">{t('documents.panel.loading')}</p>
          ) : null}
          {content.status === 'error' ? (
            <div className={styles.error}>
              <p>{t('documents.panel.failed')}</p>
              <button type="button" className={styles.actionButton} onClick={() => setReloadTick(tick => tick + 1)}>
                {t('documents.panel.retry')}
              </button>
            </div>
          ) : null}
          {ready ? (
            <>
              <DocumentMarkdown content={ready.content} />
              <DocumentSources sources={ready.sources} />
            </>
          ) : null}
        </div>
      </aside>
    </ChatDetailOverlayPortal>
  );
}
