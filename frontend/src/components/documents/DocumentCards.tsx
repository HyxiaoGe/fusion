'use client';

import { memo, useEffect, useRef, useState } from 'react';
import { ChevronRight, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { DocumentBlock } from '@/types/conversation';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import DocumentPanel from './DocumentPanel';
import styles from './DocumentCards.module.css';

interface DocumentCardsProps {
  blocks: DocumentBlock[];
  /** 这条回答是否正在生成。只有在本页看着它生成完成时才自动打开文档，翻历史、刷新进入都不打开。 */
  generating?: boolean;
  /** 本次生成是否正常完成；中断或失败时不自动打开。 */
  completed?: boolean;
}

/** 同一文档在一条回答里可能被多次修改，只展示最后一个版本的卡片。 */
export function latestDocumentBlocks(blocks: DocumentBlock[]): DocumentBlock[] {
  const latest = new Map<string, DocumentBlock>();
  for (const block of blocks) {
    const existing = latest.get(block.document_id);
    if (!existing || block.version >= existing.version) {
      latest.delete(block.document_id);
      latest.set(block.document_id, block);
    }
  }
  return [...latest.values()];
}

function DocumentCards({ blocks, generating = false, completed = false }: DocumentCardsProps) {
  const { t, i18n } = useTranslation();
  const [openBlock, setOpenBlock] = useState<DocumentBlock | null>(null);
  const cards = latestDocumentBlocks(blocks);
  const sawGeneratingRef = useRef(generating);
  const autoOpenedRef = useRef(false);
  const lastCard = cards.at(-1) ?? null;

  if (generating) sawGeneratingRef.current = true;

  // 回答在本页生成完成、页面正在前台时，自动打开最后产出的文档；只打开一次，用户关掉后不再弹。
  useEffect(() => {
    if (generating || !completed || !sawGeneratingRef.current || autoOpenedRef.current || !lastCard) return;
    autoOpenedRef.current = true;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    setOpenBlock(lastCard);
  }, [generating, completed, lastCard]);

  if (cards.length === 0) return null;

  return (
    <div className="mb-4 flex w-full max-w-6xl flex-col gap-2" data-testid="document-cards">
      {cards.map(block => (
        <button
          key={block.document_id}
          type="button"
          className={styles.card}
          onPointerMove={pointGlassLight}
          onPointerLeave={resetGlassLight}
          onClick={() => setOpenBlock(block)}
        >
          <GlassHoverLens />
          <span className={styles.icon} aria-hidden="true">
            <FileText className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-sm font-medium text-foreground">{block.title}</span>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {[
                t(block.operation === 'created' ? 'documents.card.created' : 'documents.card.edited'),
                t('documents.card.version', { version: block.version }),
                t('documents.card.chars', { chars: block.char_count.toLocaleString(i18n.language) }),
                block.change_summary,
              ].filter(Boolean).join(' · ')}
            </span>
          </span>
          <span className={styles.open}>
            {t('documents.card.open')}
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
        </button>
      ))}
      {openBlock ? (
        <DocumentPanel
          documentId={openBlock.document_id}
          initialVersion={openBlock.version}
          isOpen
          onClose={() => setOpenBlock(null)}
        />
      ) : null}
    </div>
  );
}

export default memo(DocumentCards);
