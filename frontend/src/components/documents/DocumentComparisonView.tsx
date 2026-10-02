'use client';

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, Minus, Pencil, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { DocumentVersionContent, DocumentVersionSummary } from '@/types/document';
import { getDocumentContent } from '@/lib/api/documents';
import { buildDocumentDiff, type DocumentDiffSegment } from '@/lib/documents/documentDiff';
import { buildDocumentHighlights, type DocumentHighlightRange } from '@/lib/documents/documentDiffHighlights';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import DocumentMarkdown from './DocumentMarkdown';
import panelStyles from './DocumentPanel.module.css';
import styles from './DocumentComparisonView.module.css';

interface Props {
  document: DocumentVersionContent;
  olderVersions: DocumentVersionSummary[];
  authIdentity: string | null;
}

type BaseState = { status: 'loading' } | { status: 'ready'; owner: string; data: DocumentVersionContent } | { status: 'error'; owner: string };
type Change = Extract<DocumentDiffSegment, { kind: 'change' }>;

/** 比较只读取已保存快照；不会写入文档、来源或普通阅读位置。 */
export default function DocumentComparisonView({ document: current, olderVersions, authIdentity }: Props) {
  const { t } = useTranslation();
  const [baseVersion, setBaseVersion] = useState(olderVersions[0].version);
  const [base, setBase] = useState<BaseState>({ status: 'loading' });
  const [retry, setRetry] = useState(0);
  const [active, setActive] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const changesRef = useRef<Array<HTMLElement | null>>([]);
  const navigationTopRef = useRef<number | null>(null);
  const owner = JSON.stringify([authIdentity, current.document_id, baseVersion, current.version]);

  useEffect(() => {
    const controller = new AbortController();
    setBase({ status: 'loading' });
    getDocumentContent(current.document_id, baseVersion, controller.signal)
      .then(data => {
        if (controller.signal.aborted) return;
        if (data.document_id !== current.document_id || data.version !== baseVersion) {
          setBase({ status: 'error', owner });
        } else {
          setBase({ status: 'ready', owner, data });
        }
      })
      .catch(() => { if (!controller.signal.aborted) setBase({ status: 'error', owner }); });
    return () => controller.abort();
  }, [authIdentity, current.document_id, current.version, baseVersion, owner, retry]);

  const ready = base.status === 'ready' && base.owner === owner ? base.data : null;
  const failed = base.status === 'error' && base.owner === owner;
  const diff = useMemo(() => ready ? buildDocumentDiff(ready, current) : null, [ready, current]);

  useLayoutEffect(() => {
    setActive(0);
    navigationTopRef.current = null;
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [diff]);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body || !diff?.changeCount) return;
    let frame: number | null = null;
    const onScroll = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        // 自动定位可能被文末截短；保留明确导航的目标，手动滚动后再追踪。
        if (navigationTopRef.current === body.scrollTop) return;
        navigationTopRef.current = null;
        const threshold = body.getBoundingClientRect().top + 40;
        let index = 0;
        changesRef.current.forEach((element, i) => {
          if (i < diff.changeCount && element && element.getBoundingClientRect().top <= threshold) index = i;
        });
        if (body.scrollTop > 0 && body.scrollTop + body.clientHeight >= body.scrollHeight - 2) index = diff.changeCount - 1;
        setActive(index);
      });
    };
    body.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      body.removeEventListener('scroll', onScroll);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [diff]);

  const navigate = (index: number) => {
    const body = bodyRef.current;
    const target = changesRef.current[index];
    if (!body || !target) return;
    setActive(index);
    target.focus({ preventScroll: true });
    body.scrollTo({
      top: Math.max(0, target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 12),
      behavior: 'instant',
    });
    navigationTopRef.current = body.scrollTop;
  };

  return (
    <div className={styles.view} data-testid="document-comparison-view">
      <div className={styles.controls}>
        <label className={styles.baseLabel}>
          <span>{t('documents.comparison.base')}</span>
          <span className={panelStyles.versionControl}>
            <select className={panelStyles.versionSelect} value={baseVersion} onChange={event => setBaseVersion(Number(event.target.value))}>
              {olderVersions.map(item => <option key={item.version} value={item.version}>v{item.version}</option>)}
            </select>
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
        </label>
        <span className={styles.direction}>{t('documents.comparison.direction', { before: baseVersion, after: current.version })}</span>
        <div className={styles.legend}>
          {(['added', 'removed', 'modified'] as const).map(kind => <span key={kind} data-change-kind={kind}><i aria-hidden="true" />{t(`documents.comparison.${kind === 'modified' ? 'modifyLegend' : kind}`)}</span>)}
        </div>
        <div className={styles.navigation} aria-label={t('documents.comparison.navigation')} role="group">
          <span className={styles.count} role="status">{diff ? t('documents.comparison.position', { current: diff.changeCount ? active + 1 : 0, total: diff.changeCount }) : ''}</span>
          <button type="button" className={panelStyles.actionButton} aria-label={t('documents.comparison.previous')} disabled={!diff || active === 0 || !diff.changeCount} onClick={() => navigate(active - 1)} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
            <GlassHoverLens corners={false} /><ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button type="button" className={panelStyles.actionButton} aria-label={t('documents.comparison.next')} disabled={!diff || active >= diff.changeCount - 1} onClick={() => navigate(active + 1)} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
            <GlassHoverLens corners={false} /><ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
      <div ref={bodyRef} className={styles.body} tabIndex={0} role="region" aria-label={t('documents.comparison.body')}>
        {!diff ? (
          failed ? <div className={styles.empty} role="alert"><p>{t('documents.comparison.failed')}</p><button type="button" className={panelStyles.actionButton} onClick={() => setRetry(value => value + 1)}>{t('documents.panel.retry')}</button></div>
            : <p className={styles.empty} role="status">{t('documents.comparison.loading')}</p>
        ) : (
          <>
            {diff.changeCount === 0 ? <p className={styles.empty}>{t('documents.comparison.identical')}</p> : null}
            {diff.segments.filter((segment): segment is Change => segment.kind === 'change').map(segment => {
              const kind = !segment.before ? 'added' : !segment.after ? 'removed' : 'modified';
              const Icon = kind === 'added' ? Plus : kind === 'removed' ? Minus : Pencil;
              return <Fragment key={segment.index}>
                {segment.index > 0 ? <hr className={styles.changeDivider} /> : null}
                <section ref={element => { changesRef.current[segment.index] = element; }} tabIndex={-1} className={styles.change} aria-label={t('documents.comparison.changeLabel', { index: segment.index + 1 })} data-change-index={segment.index} data-change-kind={kind}>
                <div className={styles.changeHeading}>
                  <span className={styles.changeNumber}>{t('documents.comparison.changeLabel', { index: segment.index + 1 })}</span>
                  <span className={styles.changeType}><Icon className="h-3.5 w-3.5" aria-hidden="true" />{t(`documents.comparison.${segment.scope === 'title' ? 'title' : kind}`)}</span>
                </div>
                <ChangeContent change={segment} beforeVersion={baseVersion} afterVersion={current.version} />
              </section></Fragment>;
            })}
          </>
        )}
      </div>
    </div>
  );
}

function ChangeContent({ change, beforeVersion, afterVersion }: { change: Change; beforeVersion: number; afterVersion: number }) {
  const { t } = useTranslation();
  const highlights = useMemo(() => buildDocumentHighlights(change.before, change.after), [change.before, change.after]);
  const plain = change.scope === 'title' || [change.before, change.after].every(text => !/[#>*`_\[\]|\\~]|^\s*(?:[-+]\s|\d+[.)]\s|:{3,})/m.test(text));
  const textFor = (side: 'before' | 'after') => highlightedText(change[side], highlights[side]);
  return (
    <>
      {(['before', 'after'] as const).map(side => change[side] ? (
        <div key={side} className={styles.side} data-side={side}>
          <p className={styles.sideLabel}>{t(`documents.comparison.${side}`, { version: side === 'before' ? beforeVersion : afterVersion })}</p>
          {plain ? <div className={styles.plain}>{textFor(side)}</div> : <DocumentMarkdown content={change[side]} highlights={highlights[side]} expandTabs />}
        </div>
      ) : null)}
      {!plain ? <details className={styles.raw}><summary>{t('documents.comparison.raw')}</summary>
        {(['before', 'after'] as const).map(side => change[side] ? <div key={side} className={styles.side} data-side={side}><p className={styles.sideLabel}>{t(`documents.comparison.${side}`, { version: side === 'before' ? beforeVersion : afterVersion })}</p><pre className={styles.plain}>{textFor(side)}</pre></div> : null)}
      </details> : null}
    </>
  );
}

/** 直接按原文范围拆分 React 文字节点，保留空白与 Markdown 原文。 */
function highlightedText(text: string, ranges: DocumentHighlightRange[]) {
  const result = [];
  let offset = 0;
  ranges.forEach(range => {
    if (range.start > offset) result.push(text.slice(offset, range.start));
    result.push(<mark key={range.start} data-document-change={range.kind}>{text.slice(range.start, range.end)}</mark>);
    offset = range.end;
  });
  result.push(text.slice(offset));
  return result;
}
