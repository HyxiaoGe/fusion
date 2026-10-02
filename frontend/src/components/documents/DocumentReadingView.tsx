'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { ChevronDown, ListTree } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import type { DocumentVersionContent } from '@/types/document';
import {
  readDocumentReadingPosition,
  writeDocumentReadingPosition,
  type DocumentReadingPosition,
} from '@/lib/documents/documentReadingStorage';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import DocumentMarkdown from './DocumentMarkdown';
import DocumentSources from './DocumentSources';
import panelStyles from './DocumentPanel.module.css';
import styles from './DocumentReadingView.module.css';

interface HeadingEntry {
  key: string;
  title: string;
  level: number;
  element: HTMLElement;
}

interface DocumentReadingViewProps {
  document: DocumentVersionContent;
  authIdentity: string | null;
}

/** 只用于已保存的文档；草稿流式预览不扫描目录或记录阅读位置。 */
export default function DocumentReadingView({ document: ready, authIdentity }: DocumentReadingViewProps) {
  const { t } = useTranslation();
  const id = useId().replace(/:/g, '');
  const bodyRef = useRef<HTMLDivElement>(null);
  const navigationRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const headingRefs = useRef<HeadingEntry[]>([]);
  const restoreRef = useRef<(() => void) | null>(null);
  const [saved] = useState(() => readDocumentReadingPosition(authIdentity, ready.document_id, ready.version));
  const [tabs, setTabs] = useState(saved?.tabs ?? {});
  const tabsRef = useRef(tabs);
  const [headings, setHeadings] = useState<HeadingEntry[]>([]);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [outlineOpen, setOutlineOpen] = useState(false);

  const handleTabChange = useCallback((path: string, index: number) => {
    const next = { ...tabsRef.current, [path]: index };
    tabsRef.current = next;
    setTabs(next);
  }, []);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    const markdown = body.querySelector('[data-testid="document-markdown"]');
    if (!markdown) return;
    let frame: number | null = null;
    let saveTimer: ReturnType<typeof setTimeout> | null = null;
    let restored = false;
    let position: DocumentReadingPosition = { scrollTop: 0, headingKey: null, headingOffset: 0, tabs: tabsRef.current };

    const headingTop = (heading: HTMLElement) => heading.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop;
    const capture = (updateActive = true) => {
      const entries = headingRefs.current;
      let current = entries[0];
      for (const entry of entries) {
        if (headingTop(entry.element) > body.scrollTop + 32) break;
        current = entry;
      }
      position = {
        scrollTop: body.scrollTop,
        headingKey: current?.key ?? null,
        headingOffset: current ? body.scrollTop - headingTop(current.element) : 0,
        tabs: tabsRef.current,
      };
      if (updateActive) setActiveKey(previous => previous === position.headingKey ? previous : position.headingKey);
    };
    const save = () => writeDocumentReadingPosition(authIdentity, ready.document_id, ready.version, position);
    const schedule = () => {
      if (!restored) return;
      if (frame === null) frame = requestAnimationFrame(() => {
        frame = null;
        capture();
      });
      if (saveTimer !== null) clearTimeout(saveTimer);
      saveTimer = setTimeout(() => { capture(); save(); }, 200);
    };
    const scan = () => {
      // 从实际标题元素取目录，天然排除代码、表格文本和未选中标签页。
      const entries = Array.from(markdown.querySelectorAll<HTMLElement>('[data-document-heading-key]'))
        .filter(element => !element.closest('[hidden], [aria-hidden="true"]') && element.textContent?.trim())
        .map(element => {
          const key = element.dataset.documentHeadingKey!;
          element.id = `document-${id}-${key}`;
          element.tabIndex = -1;
          return { key, title: element.textContent!.trim(), level: Number(element.tagName.slice(1)), element };
        });
      headingRefs.current = entries;
      setHeadings(entries);
    };

    scan();
    restoreRef.current = () => {
      const anchor = headingRefs.current.find(entry => entry.key === saved?.headingKey);
      body.scrollTop = saved ? Math.max(0, anchor ? headingTop(anchor.element) + saved.headingOffset : saved.scrollTop) : 0;
      restored = true;
      capture();
    };
    const observer = new MutationObserver(() => { scan(); schedule(); });
    observer.observe(markdown, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
    body.addEventListener('scroll', schedule, { passive: true });
    const handlePageHide = () => { if (restored) { capture(); save(); } };
    window.addEventListener('pagehide', handlePageHide);
    return () => {
      observer.disconnect();
      body.removeEventListener('scroll', schedule);
      window.removeEventListener('pagehide', handlePageHide);
      if (frame !== null) cancelAnimationFrame(frame);
      if (saveTimer !== null) clearTimeout(saveTimer);
      restoreRef.current = null;
      // 重开时缓存正文可能立刻被加载态替换；未完成恢复的临时挂载不能覆盖旧坐标。
      if (!restored) return;
      // 卸载后 DOM 坐标已无意义，保留最后一次有效快照；仍连接时补收尾滚动。
      if (body.isConnected) capture(false);
      else position = {
        ...position,
        headingOffset: position.headingOffset + body.scrollTop - position.scrollTop,
        scrollTop: body.scrollTop,
        tabs: tabsRef.current,
      };
      save();
    };
  }, [authIdentity, id, ready.document_id, ready.version, saved]);

  // 目录栏出现后正文高度才稳定，恢复在这一帧完成，避免文末坐标被提前截短。
  useLayoutEffect(() => {
    if (headings !== headingRefs.current || !restoreRef.current) return;
    restoreRef.current();
    restoreRef.current = null;
  }, [headings]);

  useEffect(() => {
    if (!outlineOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !navigationRef.current?.contains(event.target)) setOutlineOpen(false);
    };
    window.document.addEventListener('pointerdown', handlePointerDown);
    return () => window.document.removeEventListener('pointerdown', handlePointerDown);
  }, [outlineOpen]);

  const jumpToHeading = (entry: HeadingEntry) => {
    const body = bodyRef.current;
    if (!body) return;
    body.scrollTop = Math.max(0, entry.element.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 24);
    setActiveKey(entry.key);
    setOutlineOpen(false);
    entry.element.focus({ preventScroll: true });
    body.dispatchEvent(new Event('scroll'));
  };

  const current = headings.find(entry => entry.key === activeKey);
  const minimumLevel = Math.min(...headings.map(entry => entry.level));
  return (
    <div className={styles.reading}>
      {headings.length > 0 ? (
        <div ref={navigationRef} className={styles.navigation} onKeyDown={event => {
          if (event.key === 'Escape' && outlineOpen) {
            event.stopPropagation();
            setOutlineOpen(false);
            toggleRef.current?.focus();
          }
        }}>
          <button
            ref={toggleRef}
            type="button"
            className={styles.toggle}
            aria-expanded={outlineOpen}
            aria-controls={`document-outline-${id}`}
            onClick={() => setOutlineOpen(open => !open)}
            onPointerMove={pointGlassLight}
            onPointerLeave={resetGlassLight}
          >
            <GlassHoverLens corners={false} />
            <ListTree className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('documents.reading.contents')}</span>
            <ChevronDown className={styles.chevron} aria-hidden="true" />
          </button>
          <span className={styles.current} title={current?.title}>
            <span className={styles.currentLabel}>{t('documents.reading.current')}</span>
            <span data-testid="document-current-section">{current?.title}</span>
          </span>
          {outlineOpen ? (
            <nav id={`document-outline-${id}`} className={styles.outline} aria-label={t('documents.reading.outlineLabel')}>
              <p className={styles.outlineTitle}>{t('documents.reading.outlineLabel')}</p>
              <ol>
                {headings.map(entry => (
                  <li key={entry.key}>
                    <button
                      type="button"
                      aria-controls={entry.element.id}
                      aria-current={entry.key === activeKey ? 'location' : undefined}
                      className={styles.outlineItem}
                      style={{ paddingInlineStart: `${12 + (entry.level - minimumLevel) * 12}px` }}
                      onClick={() => jumpToHeading(entry)}
                    >
                      {entry.title}
                    </button>
                  </li>
                ))}
              </ol>
            </nav>
          ) : null}
        </div>
      ) : null}
      <div ref={bodyRef} className={cn('min-h-0 flex-1 overflow-y-auto', panelStyles.body, styles.body)} data-testid="document-reading-body" role="region" aria-label={t('documents.reading.bodyLabel')}>
        <DocumentMarkdown content={ready.content} tabSelections={tabs} onTabChange={handleTabChange} />
        <DocumentSources sources={ready.sources} />
      </div>
    </div>
  );
}
