'use client';

import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, BookOpen, Check, ChevronDown, ExternalLink, Globe2, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import {
  groupAnswerEvidenceSources,
  type AnswerEvidenceKnowledgeDocument,
  type AnswerEvidenceSidebarIssueItem,
  type AnswerEvidenceSidebarModel,
  type AnswerEvidenceSidebarUsedItem,
} from './answerEvidenceSidebarModel';
import { useChatDetailOverlayRegistration } from './ChatDetailOverlayContext';
import { ChatDetailOverlayPortal } from './ChatDetailOverlayPortal';
import KnowledgeEvidenceSourcePreview from './KnowledgeEvidenceSourcePreview';
import styles from './AnswerEvidenceSidebar.module.css';

interface AnswerEvidenceSidebarProps {
  model: AnswerEvidenceSidebarModel | null;
  isOpen: boolean;
  onClose: () => void;
  highlightIndex?: number;
  highlightCitationIndex?: number;
  highlightTick?: number;
}

const EMPTY_USED_ITEMS: AnswerEvidenceSidebarUsedItem[] = [];

const sourceKey = (item: AnswerEvidenceSidebarUsedItem) => JSON.stringify([
  item.kind, item.id, item.citationIndex, item.url, item.title,
  item.knowledge?.knowledgeBaseId, item.knowledge?.documentId,
  item.knowledge?.indexVersion, item.knowledge?.chunkId,
]);

export default function AnswerEvidenceSidebar({
  model,
  isOpen,
  onClose,
  highlightIndex,
  highlightCitationIndex,
  highlightTick,
}: AnswerEvidenceSidebarProps) {
  const { t } = useTranslation();
  const itemRefs = useRef<Array<HTMLDivElement | null>>([]);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const usedItems = model?.usedItems ?? EMPTY_USED_ITEMS;
  const candidateItems = model?.candidateItems ?? EMPTY_USED_ITEMS;
  // 展示顺序即定位顺序：已使用的知识库文件在前、网页在后；候选里网页在前，知识库段落收在末尾。
  const usedGroups = groupAnswerEvidenceSources(usedItems);
  const candidateGroups = groupAnswerEvidenceSources(candidateItems);
  const orderedUsed = [...usedGroups.knowledgeDocuments.flatMap(document => document.items), ...usedGroups.webItems];
  const candidateKnowledgeStart = orderedUsed.length + candidateGroups.webItems.length;
  const sourceItems = [
    ...orderedUsed,
    ...candidateGroups.webItems,
    ...candidateGroups.knowledgeDocuments.flatMap(document => document.items),
  ];
  const flatIndexes = new Map(sourceItems.map((item, index) => [item, index]));
  // 只有还有其他来源可看时才折叠知识库候选，避免整栏只剩一个收起的分组。
  const collapseKnowledgeCandidates = candidateGroups.knowledgeDocuments.length > 0
    && (orderedUsed.length > 0 || candidateGroups.webItems.length > 0);
  const [knowledgeCandidatesOpen, setKnowledgeCandidatesOpen] = useState(false);
  const selectionRequest = JSON.stringify([isOpen, highlightCitationIndex, highlightIndex, highlightTick]);
  const [selection, setSelection] = useState<{ key: string; request: string; tick: number } | null>(null);
  const incomingIndex = highlightCitationIndex != null || (typeof highlightIndex === 'number' && highlightIndex >= 0)
    ? sourceItems.findIndex(item => highlightCitationIndex != null
      ? item.citationIndex === highlightCitationIndex : item.sourceIndex === highlightIndex)
    : -1;
  const localIndex = selection?.request === selectionRequest
    ? sourceItems.findIndex(item => sourceKey(item) === selection.key) : -1;
  const selectedIndex = localIndex >= 0 ? localIndex : incomingIndex;
  const selectionTick = localIndex >= 0 ? selection?.tick ?? 0 : 0;
  const selectSource = (item: AnswerEvidenceSidebarUsedItem) => setSelection(previous => ({
    key: sourceKey(item), request: selectionRequest, tick: (previous?.tick ?? 0) + 1,
  }));
  useChatDetailOverlayRegistration(isOpen && Boolean(model?.isRenderable));

  useEffect(() => {
    if (!isOpen) {
      setSelection(null);
      setKnowledgeCandidatesOpen(false);
    }
  }, [isOpen]);

  const selectedInKnowledgeCandidates = selectedIndex >= candidateKnowledgeStart;
  useEffect(() => {
    // 引用指向被收起的知识库候选时先展开，滚动定位在其后执行。
    if (isOpen && selectedInKnowledgeCandidates) setKnowledgeCandidatesOpen(true);
  }, [isOpen, selectedInKnowledgeCandidates, selectionTick, highlightTick]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;

    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButtonRef.current?.focus();

    return () => {
      previousFocusRef.current?.focus();
      previousFocusRef.current = null;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || selectedIndex < 0) return;
    const element = itemRefs.current[selectedIndex];
    if (!element) return;
    const timer = setTimeout(() => {
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      element.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
    }, 100);
    return () => clearTimeout(timer);
  }, [selectedIndex, selectionTick, highlightTick, isOpen, usedItems, candidateItems, knowledgeCandidatesOpen]);

  if (!isOpen || !model?.isRenderable) {
    return null;
  }

  const renderSourceItem = (item: AnswerEvidenceSidebarUsedItem, compact: boolean) => {
    const index = flatIndexes.get(item) ?? -1;
    return (
      <UsedSourceItem
        key={item.id}
        ref={(element) => { if (index >= 0) itemRefs.current[index] = element; }}
        item={item}
        compact={compact}
        highlighted={index >= 0 && index === selectedIndex}
        highlightTick={(highlightTick ?? 0) + selectionTick}
        onSelect={() => selectSource(item)}
      />
    );
  };

  const summary = [
    model.summary.usedCount > 0 ? t('chatBody.evidencePanel.usedCount', { count: model.summary.usedCount })
      : model.summary.candidateCount > 0 ? t('chatBody.evidencePanel.candidateCount', { count: model.summary.candidateCount })
        : t('chatBody.evidencePanel.readCount', { count: model.summary.urlCount }),
    model.summary.usedCount > 0 && model.summary.candidateCount > 0
      ? t('chatBody.evidencePanel.candidateCount', { count: model.summary.candidateCount }) : '',
    (model.summary.usedCount > 0 || model.summary.candidateCount > 0) && model.summary.urlCount > 0
      ? t('chatBody.evidencePanel.readCount', { count: model.summary.urlCount }) : '',
  ].filter(Boolean).join(' · ');

  return (
    <ChatDetailOverlayPortal>
      <button
        type="button"
        aria-label={t('chatBody.evidencePanel.closeBackdrop')}
        data-chat-detail-overlay-surface="true"
        className="fixed inset-0 z-40 cursor-default bg-black/20 p-0 transition-opacity"
        onClick={onClose}
      />
      <aside
        data-testid="answer-evidence-sidebar"
        role="dialog"
        aria-modal="true"
        aria-label={t('chatBody.evidencePanel.title')}
        data-chat-detail-overlay-surface="true"
        className={cn('fixed inset-y-0 right-0 z-50 flex w-[440px] max-w-[100vw] transform flex-col transition-transform duration-300 ease-in-out', styles.panel)}
      >
        <header className={styles.header}>
          <GlassHoverLens corners={false} />
          <div className={styles.heading}>
            <span className={styles.headerIcon}><BookOpen size={19} aria-hidden="true" /></span>
            <div className="min-w-0">
              <h3 className={styles.panelTitle}>{t('chatBody.evidencePanel.title')}</h3>
              <p className={styles.summary}>{summary}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {model.summary.issueCount > 0 ? (
              <span className={styles.issueCount}>
                {t('chatBody.evidencePanel.issueCount', { count: model.summary.issueCount })}
              </span>
            ) : null}
            <button
              ref={closeButtonRef}
              type="button"
              aria-label={t('chatBody.evidencePanel.close')}
              onClick={onClose}
              className={styles.iconButton}
              onPointerMove={pointGlassLight}
              onPointerLeave={resetGlassLight}
            >
              <GlassHoverLens corners={false} />
              <X className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className={cn('min-h-0 flex-1 overflow-y-auto px-4 py-4', styles.body)}>
          <SearchQuerySection queries={model.searchQueries} />

          {orderedUsed.length > 0 ? (
            <section>
              <div className={styles.sectionHeading}>
                <h4>{t('chatBody.evidencePanel.usedSources')}</h4>
                <span className={styles.sectionCount}>{orderedUsed.length}</span>
              </div>
              <SourceGroupList
                knowledgeDocuments={usedGroups.knowledgeDocuments}
                webItems={usedGroups.webItems}
                labelGroups={usedGroups.knowledgeDocuments.length > 0 && usedGroups.webItems.length > 0}
                renderItem={renderSourceItem}
              />
            </section>
          ) : null}

          {candidateItems.length > 0 ? (
            <section className={orderedUsed.length > 0 ? 'mt-5' : undefined}>
              <div className={styles.sectionHeading}>
                <h4>{t('chatBody.evidencePanel.candidateSources')}</h4>
                <span className={styles.sectionCount}>{candidateItems.length}</span>
              </div>
              {collapseKnowledgeCandidates ? (
                <>
                  <div className={styles.sourceList}>
                    {candidateGroups.webItems.map(item => renderSourceItem(item, false))}
                  </div>
                  <details
                    className={cn(styles.queryBox, styles.collapsedKnowledge)}
                    open={knowledgeCandidatesOpen}
                    onToggle={event => setKnowledgeCandidatesOpen(event.currentTarget.open)}
                  >
                    <summary tabIndex={0} className={styles.queryToggle} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
                      <GlassHoverLens corners={false} />
                      <BookOpen size={14} aria-hidden="true" />
                      <span>{t('chatBody.evidencePanel.knowledgeCandidates', {
                        files: candidateGroups.knowledgeDocuments.length,
                        count: candidateItems.length - candidateGroups.webItems.length,
                      })}</span>
                      <ChevronDown className={styles.queryChevron} size={14} aria-hidden="true" />
                    </summary>
                    <div className={styles.collapsedKnowledgeBody}>
                      <SourceGroupList
                        knowledgeDocuments={candidateGroups.knowledgeDocuments}
                        webItems={EMPTY_USED_ITEMS}
                        labelGroups={false}
                        renderItem={renderSourceItem}
                      />
                    </div>
                  </details>
                </>
              ) : (
                <SourceGroupList
                  knowledgeDocuments={candidateGroups.knowledgeDocuments}
                  webItems={candidateGroups.webItems}
                  labelGroups={candidateGroups.knowledgeDocuments.length > 0 && candidateGroups.webItems.length > 0}
                  renderItem={renderSourceItem}
                />
              )}
            </section>
          ) : null}

          {usedItems.length === 0 && candidateItems.length === 0 ? (
            <section className={cn('px-3 py-2 text-xs text-muted-foreground', styles.empty)}>
              {t('chatBody.evidencePanel.empty')}
            </section>
          ) : null}

          {model.issueItems.length > 0 ? (
            <section className="mt-5">
              <div className={cn(styles.sectionHeading, styles.issueHeading)}>
                <h4>{t('chatBody.evidencePanel.issueSources')}</h4>
                <span className={styles.sectionCount}>{model.issueItems.length}</span>
              </div>
              <div className={styles.sourceList}>
                {model.issueItems.map(item => (
                  <IssueSourceItem key={item.id} item={item} />
                ))}
              </div>
            </section>
          ) : null}

        </div>
      </aside>
    </ChatDetailOverlayPortal>
  );
}

function SourceGroupList({
  knowledgeDocuments,
  webItems,
  labelGroups,
  renderItem,
}: {
  knowledgeDocuments: AnswerEvidenceKnowledgeDocument[];
  webItems: AnswerEvidenceSidebarUsedItem[];
  labelGroups: boolean;
  renderItem: (item: AnswerEvidenceSidebarUsedItem, compact: boolean) => React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.sourceList}>
      {labelGroups ? <p className={styles.groupLabel}>{t('chatBody.evidencePanel.knowledgeGroup')}</p> : null}
      {knowledgeDocuments.map(document => (
        <div key={document.key} className={styles.documentGroup} data-testid="answer-evidence-knowledge-document">
          <div className={styles.documentHeader}>
            <BookOpen className={styles.documentIcon} size={15} aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className={styles.documentTitle} title={document.filename}>{document.filename}</span>
              <span className={styles.documentMeta}>
                {document.knowledgeBaseName} · {t('chatBody.evidencePanel.documentChunks', { count: document.items.length })}
              </span>
            </span>
          </div>
          <div className={styles.documentChunks}>
            {document.items.map(item => renderItem(item, true))}
          </div>
        </div>
      ))}
      {labelGroups ? <p className={styles.groupLabel}>{t('chatBody.evidencePanel.webGroup')}</p> : null}
      {webItems.map(item => renderItem(item, false))}
    </div>
  );
}

function SearchQuerySection({ queries }: { queries: string[] }) {
  const { t } = useTranslation();
  if (queries.length === 0) return null;

  return (
    <details className={styles.queryBox}>
      <summary tabIndex={0} className={styles.queryToggle} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
        <GlassHoverLens corners={false} />
        <Search size={14} aria-hidden="true" />
        <span>{t('chatBody.evidencePanel.queries')}</span>
        <span className={styles.sectionCount}>{queries.length}</span>
        <ChevronDown className={styles.queryChevron} size={14} aria-hidden="true" />
      </summary>
      <div className={styles.queries}>
        <div className="space-y-1.5">
          {queries.map((query, index) => (
            <div key={query} className="flex min-w-0 items-start gap-2 text-xs">
              <span className="mt-0.5 flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full border border-border/40 text-[10px] text-muted-foreground">
                {index + 1}
              </span>
              <span className="min-w-0 break-words text-foreground" title={query}>
                {query}
              </span>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}

const UsedSourceItem = React.forwardRef<HTMLDivElement, {
  item: AnswerEvidenceSidebarUsedItem;
  compact?: boolean;
  highlighted: boolean;
  highlightTick?: number;
  onSelect: () => void;
}>(({ item, compact = false, highlighted, highlightTick = 0, onSelect }, ref) => {
  const { t } = useTranslation();
  // 同一章节下常有多个段落，章节名之外总带块序号，段落之间才分得开。
  const knowledgeLocation = item.knowledge
    ? [
      item.knowledge.section,
      t('chatBody.evidencePanel.chunk', { number: item.knowledge.ordinal + 1 }),
      item.knowledge.page !== null ? t('chatBody.evidencePanel.page', { number: item.knowledge.page }) : null,
    ].filter(Boolean).join(' · ')
    : '';
  return (
    <div
      ref={ref}
      data-testid={item.kind === 'search'
        ? `answer-evidence-used-search-${item.sourceIndex}`
        : item.kind === 'knowledge'
          ? `answer-evidence-used-knowledge-${item.sourceIndex}`
          : undefined}
      data-highlighted={highlighted}
      aria-current={highlighted ? 'true' : undefined}
      className={cn(styles.sourceCard, compact && styles.compactCard)}
    >
      <div className={styles.sourceRow} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
        <GlassHoverLens />
        <button
          type="button"
          className={styles.sourceSelect}
          aria-pressed={highlighted}
          aria-label={item.citationIndex != null
            ? t('chatBody.evidencePanel.selectCitation', { number: item.citationIndex, title: item.title })
            : t('chatBody.evidencePanel.selectSource', { title: item.title })}
          onClick={onSelect}
        >
          <span className={styles.sourceIcon}>
            {item.citationIndex != null ? <span className={styles.citationNumber}>{item.citationIndex}</span> : <UsedSourceIcon item={item} />}
          </span>
          <span className={styles.sourceText}>
            {compact ? (
              <span className={styles.compactTitle} title={knowledgeLocation}>{knowledgeLocation}</span>
            ) : (<>
            <span className={styles.sourceMeta}>
              <span className={cn('shrink-0', styles.kindBadge)}>
                {t(`chatBody.evidencePanel.kinds.${item.kind}`)}
              </span>
              {item.deepRead ? (
                <span className="shrink-0 rounded-full border border-success/30 bg-success/5 px-1.5 py-0.5 text-[10px] text-success">
                  {t('chatBody.evidencePanel.deepRead')}
                </span>
              ) : null}
              {item.citationIndex != null && item.favicon ? (
                <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center" aria-hidden="true">
                  <UsedSourceIcon item={item} />
                </span>
              ) : null}
              <span className="min-w-0 truncate text-[10px] text-muted-foreground">{item.domain}</span>
            </span>
            <span className={styles.title} title={item.title}>{item.title}</span>
            {item.knowledge ? (
              <span className={styles.knowledgeLocation}>{knowledgeLocation}</span>
            ) : null}
            </>)}
            {highlighted ? <span className={styles.currentMark}><Check size={12} aria-hidden="true" />{t('chatBody.evidencePanel.current')}</span> : null}
          </span>
        </button>
        {item.kind !== 'knowledge' ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={t('chatBody.evidencePanel.openSource', { title: item.title })}
            className={cn(styles.iconButton, styles.externalLink)}
            onPointerMove={pointGlassLight}
            onPointerLeave={resetGlassLight}
          >
            <GlassHoverLens corners={false} />
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
          </a>
        ) : null}
      </div>
      {item.knowledge ? (
        <KnowledgeEvidenceSourcePreview
          source={item.knowledge}
          autoOpen={highlighted}
          autoOpenTick={highlightTick}
        />
      ) : null}
    </div>
  );
});
UsedSourceItem.displayName = 'UsedSourceItem';

function UsedSourceIcon({ item }: { item: AnswerEvidenceSidebarUsedItem }) {
  if (item.favicon) {
    return (
      // 外部站点 favicon 尺寸很小且需保留原始 URL，Next Image 优化没有收益。
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.favicon}
        alt=""
        className="h-4 w-4 rounded-sm object-contain"
        onError={(event) => {
          event.currentTarget.style.display = 'none';
        }}
      />
    );
  }

  const Icon = item.kind === 'search' ? Search : item.kind === 'knowledge' ? BookOpen : Globe2;
  return <Icon className="h-4 w-4" aria-hidden="true" />;
}

function IssueSourceItem({ item }: { item: AnswerEvidenceSidebarIssueItem }) {
  const { t } = useTranslation();
  return (
    <div className={cn('flex min-w-0 gap-3 px-3 py-2', styles.issueCard)}>
      <span className={cn(
        'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
        item.status === 'failed' ? 'text-danger' : item.status === 'degraded' ? 'text-warn' : 'text-muted-foreground',
      )}>
        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex min-w-0 flex-wrap items-center gap-2">
          <span className={cn('shrink-0', styles.kindBadge)}>
            {t(`chatBody.evidencePanel.kinds.${item.kind}`)}
          </span>
          <StatusBadge status={item.status} />
          {item.domain ? (
            <span className="min-w-0 truncate text-[10px] text-muted-foreground">{item.domain}</span>
          ) : null}
        </div>
        <p className={cn('line-clamp-2 text-sm font-medium text-foreground', styles.title)} title={item.title}>
          {item.title}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{item.reason}</p>
      </div>
      {item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t('chatBody.evidencePanel.openSource', { title: item.title })}
          className={cn('mt-0.5', styles.iconButton)}
        >
          <ExternalLink className="h-4 w-4" aria-hidden="true" />
        </a>
      ) : null}
    </div>
  );
}

function StatusBadge({ status }: { status: AnswerEvidenceSidebarIssueItem['status'] }) {
  const { t } = useTranslation();
  const text = t(`chatBody.evidencePanel.status.${status === 'degraded' ? 'degraded' : status === 'interrupted' ? 'interrupted' : 'unused'}`);
  return (
    <span className={cn(
      'shrink-0 rounded-full border px-1.5 py-0.5 text-[10px]',
      status === 'failed' || status === 'unavailable' || status === 'empty' ? 'border-danger/30 text-danger'
        : status === 'degraded' ? 'border-warn/30 text-warn'
          : 'border-border/40 text-muted-foreground',
    )}>
      {text}
    </span>
  );
}
