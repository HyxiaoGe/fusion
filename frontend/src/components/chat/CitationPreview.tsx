'use client';

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { BookOpen, Globe2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { SearchSourceSummary } from '@/types/conversation';
import { useHasOpenChatDetailOverlay } from './ChatDetailOverlayContext';
import styles from './CitationPreview.module.css';

const PREVIEW_OPEN_EVENT = 'fusion:citation-preview-open';
const sourceKey = (source: SearchSourceSummary) => JSON.stringify([
  source.kind, source.evidence_id, source.citation_index, source.url, source.title,
]);

interface ActiveCitation {
  element: HTMLElement;
  index: number;
  number: number;
  key: string;
}

/** 一段回答共享一个按需挂载的浮层，引用本身不创建定位器，也不因预览重解析正文。 */
export default function CitationPreview({ children, className, sources }: {
  children: ReactNode;
  className: string;
  sources: SearchSourceSummary[];
}) {
  const id = useId();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerFocus = useRef<HTMLElement | null>(null);
  const [active, setActive] = useState<ActiveCitation | null>(null);
  const hasDetailOverlay = useHasOpenChatDetailOverlay();
  const source = active ? sources[active.index] : undefined;
  const visible = active && source && sourceKey(source) === active.key && !hasDetailOverlay
    ? active : null;

  const clearTimers = useCallback(() => {
    if (openTimer.current !== null) clearTimeout(openTimer.current);
    if (closeTimer.current !== null) clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  }, []);
  const close = useCallback(() => { clearTimers(); setActive(null); }, [clearTimers]);
  useEffect(() => clearTimers, [clearTimers]);

  const findCitation = (target: EventTarget | null): ActiveCitation | null => {
    if (!(target instanceof Element) || hasDetailOverlay) return null;
    const element = target.closest<HTMLElement>('[data-citation-index]');
    if (!element || !wrapperRef.current?.contains(element)) return null;
    const index = Number(element.dataset.citationIndex);
    const number = Number(element.dataset.citationNumber);
    const candidate = sources[index];
    return candidate && Number.isInteger(index) && Number.isInteger(number)
      ? { element, index, number, key: sourceKey(candidate) } : null;
  };
  const show = (citation: ActiveCitation, delayed: boolean) => {
    clearTimers();
    if (active?.element === citation.element && active.key === citation.key) return;
    if (!delayed) { setActive(citation); return; }
    const rect = citation.element.getBoundingClientRect();
    openTimer.current = setTimeout(() => {
      openTimer.current = null;
      const next = citation.element.getBoundingClientRect();
      // 指针等待期间发生滚动或正文替换时，不在旧位置弹出预览。
      if (citation.element.isConnected && Math.abs(rect.top - next.top) < 2 && Math.abs(rect.left - next.left) < 2) {
        setActive(citation);
      }
    }, 250);
  };
  const leave = () => {
    clearTimers();
    closeTimer.current = setTimeout(close, 150);
  };

  useLayoutEffect(() => {
    if (active && (!visible || !active.element.isConnected || !wrapperRef.current?.contains(active.element))) close();
  });

  useLayoutEffect(() => {
    if (!visible) return;
    const element = visible.element;
    const descriptions = element.getAttribute('aria-describedby')?.split(/\s+/).filter(Boolean) ?? [];
    element.setAttribute('aria-describedby', [...descriptions, id].join(' '));
    return () => {
      const remaining = element.getAttribute('aria-describedby')?.split(/\s+/).filter(value => value && value !== id) ?? [];
      if (remaining.length) element.setAttribute('aria-describedby', remaining.join(' '));
      else element.removeAttribute('aria-describedby');
    };
  }, [id, visible]);

  useEffect(() => {
    if (!visible) return;
    document.dispatchEvent(new CustomEvent(PREVIEW_OPEN_EVENT, { detail: id }));
    const handleOtherPreview = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== id) close();
    };
    const handleScroll = (event: Event) => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-citation-preview]')) close();
    };
    document.addEventListener(PREVIEW_OPEN_EVENT, handleOtherPreview);
    window.addEventListener('scroll', handleScroll, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener(PREVIEW_OPEN_EVENT, handleOtherPreview);
      window.removeEventListener('scroll', handleScroll, true);
      window.removeEventListener('resize', close);
    };
  }, [close, id, visible]);

  return (
    <>
      <div
        ref={wrapperRef}
        className={className}
        onPointerOver={event => {
          if (event.pointerType === 'touch') return;
          const citation = findCitation(event.target);
          if (citation && !(event.relatedTarget instanceof Node && citation.element.contains(event.relatedTarget))) show(citation, true);
        }}
        onPointerOut={event => {
          const citation = findCitation(event.target);
          if (citation && !(event.relatedTarget instanceof Node && citation.element.contains(event.relatedTarget))) leave();
        }}
        onPointerDownCapture={event => { pointerFocus.current = findCitation(event.target)?.element ?? null; close(); }}
        onFocusCapture={event => {
          const citation = findCitation(event.target);
          if (citation && pointerFocus.current !== citation.element) show(citation, false);
          pointerFocus.current = null;
        }}
        onBlurCapture={event => {
          pointerFocus.current = null;
          if (!(event.relatedTarget instanceof Element && event.relatedTarget.closest('[data-citation-preview]'))) close();
        }}
        onClickCapture={close}
        onKeyDownCapture={event => {
          if (event.key === 'Escape' && (active || openTimer.current !== null)) {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
      >
        {children}
      </div>
      {visible && source ? (
        <Popover.Root open onOpenChange={open => { if (!open) close(); }}>
          <Popover.Anchor virtualRef={{ current: visible.element }} />
          <Popover.Portal>
            <Popover.Content
              key={`${visible.index}:${visible.number}`}
              id={id}
              role="tooltip"
              data-citation-preview="true"
              className={styles.preview}
              side="top"
              align="start"
              sideOffset={8}
              collisionPadding={12}
              hideWhenDetached
              onOpenAutoFocus={event => event.preventDefault()}
              onCloseAutoFocus={event => event.preventDefault()}
              onFocusOutside={event => event.preventDefault()}
              onEscapeKeyDown={event => { event.preventDefault(); event.stopPropagation(); close(); }}
              onPointerEnter={clearTimers}
              onPointerLeave={leave}
            >
              <PreviewContent source={source} number={visible.number} triggerTag={visible.element.tagName} />
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      ) : null}
    </>
  );
}

function PreviewContent({ source, number, triggerTag }: {
  source: SearchSourceSummary; number: number; triggerTag: string;
}) {
  const { t } = useTranslation();
  const knowledge = source.kind === 'knowledge';
  let domain = t('chatBody.citation.knowledge');
  if (!knowledge) {
    try { domain = new URL(source.url).hostname.replace(/^www\./, ''); }
    catch { domain = t('chatBody.citation.web'); }
  }
  const snippet = source.snippet?.trim().slice(0, 320);
  const Icon = knowledge ? BookOpen : Globe2;
  return (
    <>
      <div className={styles.header}>
        <span className={styles.number}>{number}</span>
        <span className={styles.domain}><Icon size={13} aria-hidden="true" />{domain}</span>
      </div>
      <p className={styles.title}>{source.title}</p>
      <p className={styles.summaryLabel}>{t('chatBody.citation.summary')}</p>
      <p className={styles.snippet}>{snippet || t('chatBody.citation.noSummary')}</p>
      {triggerTag === 'BUTTON' || triggerTag === 'A' ? (
        <p className={styles.hint}>{t(triggerTag === 'BUTTON' ? 'chatBody.citation.openHint' : 'chatBody.citation.linkHint')}</p>
      ) : null}
    </>
  );
}
