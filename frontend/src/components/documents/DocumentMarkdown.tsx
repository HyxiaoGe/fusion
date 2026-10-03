'use client';

import React, { createContext, useContext, useMemo, useState } from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { Info, Lightbulb, TriangleAlert, Wallet } from 'lucide-react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { useTranslation } from 'react-i18next';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';
import type { DocumentHighlightRange } from '@/lib/documents/documentDiffHighlights';
import { createDocumentHighlightPlugin, mapDocumentMarkdownHighlights } from '@/lib/documents/documentHighlightRenderer';
import {
  parseDocumentDirectives,
  type DocumentSegment,
  type DocumentTab,
  type DocumentSourcePositions,
} from '@/lib/documents/documentDirectives';
import markdownStyles from '@/components/chat/MarkdownRenderer.module.css';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import DocumentCodeBlock from './DocumentCodeBlock';
import styles from './DocumentMarkdown.module.css';

interface DocumentMarkdownProps {
  content: string;
  /** 导出静态 HTML 时展开全部 tab，不依赖交互。 */
  expandTabs?: boolean;
  className?: string;
  tabSelections?: Record<string, number>;
  onTabChange?: (path: string, index: number) => void;
  /** 仅只读差异视图传入，范围以 content 的原始字符位置为准。 */
  highlights?: DocumentHighlightRange[];
}

const ReadingTabsContext = createContext<{
  selections: Record<string, number>;
  onChange?: (path: string, index: number) => void;
} | null>(null);
const HighlightsContext = createContext<Record<string, DocumentHighlightRange[]> | null>(null);

export default function DocumentMarkdown({ content, expandTabs = false, className, tabSelections, onTabChange, highlights }: DocumentMarkdownProps) {
  const { segments, highlightsByPath } = useMemo(() => {
    const positions: DocumentSourcePositions | undefined = highlights?.length ? new WeakMap() : undefined;
    const segments = parseDocumentDirectives(content, positions);
    return { segments, highlightsByPath: positions ? mapDocumentMarkdownHighlights(content, segments, highlights!, positions) : null };
  }, [content, highlights]);
  const readingTabs = useMemo(() => tabSelections ? { selections: tabSelections, onChange: onTabChange } : null, [tabSelections, onTabChange]);
  return (
    <div className={cn(markdownStyles.content, styles.document, 'fdoc', className)} data-testid="document-markdown">
      <ReadingTabsContext.Provider value={readingTabs}>
        <HighlightsContext.Provider value={highlightsByPath}>
          <Segments segments={segments} expandTabs={expandTabs} path="root" />
        </HighlightsContext.Provider>
      </ReadingTabsContext.Provider>
    </div>
  );
}

function Segments({ segments, expandTabs, path }: { segments: DocumentSegment[]; expandTabs: boolean; path: string }) {
  return (
    <>
      {segments.map((segment, index) => (
        <SegmentView key={index} segment={segment} expandTabs={expandTabs} path={`${path}.${index}`} />
      ))}
    </>
  );
}

function SegmentView({ segment, expandTabs, path }: { segment: DocumentSegment; expandTabs: boolean; path: string }) {
  const { t } = useTranslation();
  switch (segment.kind) {
    case 'markdown':
      return <MarkdownBlock text={segment.text} staticView={expandTabs} path={path} />;
    case 'callout': {
      const Icon = { tip: Lightbulb, info: Info, warning: TriangleAlert, price: Wallet }[segment.variant];
      return (
        <aside className={cn(styles.callout, 'fdoc-callout')} data-variant={segment.variant}>
          <div className={cn(styles.calloutHeading, 'fdoc-callout-heading')}>
            <span className={cn(styles.calloutIcon, 'fdoc-callout-icon')}><Icon aria-hidden="true" /></span>
            <p className={cn(styles.calloutLabel, 'fdoc-callout-label')}><HighlightedField text={segment.label ?? t(`documents.callout.${segment.variant}`)} path={`${path}.label`} /></p>
          </div>
          <div className={cn(styles.calloutBody, 'fdoc-callout-body')}>
            <Segments segments={segment.children} expandTabs={expandTabs} path={`${path}.children`} />
          </div>
        </aside>
      );
    }
    case 'timeline':
      return (
        <section className={cn(styles.timeline, 'fdoc-timeline')}>
          {segment.label ? <p className={cn(styles.blockLabel, 'fdoc-block-label')}><HighlightedField text={segment.label} path={`${path}.label`} /></p> : null}
          <Segments segments={segment.children} expandTabs={expandTabs} path={`${path}.children`} />
        </section>
      );
    case 'stats':
      if (segment.items.length === 0) return <MarkdownBlock text={segment.fallback} staticView={expandTabs} path={path} />;
      return (
        <section className={cn(styles.statsSection, 'fdoc-stats-section')}>
          {segment.label ? <p className={cn(styles.blockLabel, 'fdoc-block-label')}><HighlightedField text={segment.label} path={`${path}.label`} /></p> : null}
          <div className={cn(styles.stats, 'fdoc-stats')}>
            {segment.items.map((item, index) => (
              <div key={`${item.label}-${index}`} className={cn(styles.stat, 'fdoc-stat')}>
                <span
                  className={cn(styles.statValue, 'fdoc-stat-value')}
                  data-long-value={Array.from(item.value).length > 20 || undefined}
                ><HighlightedField text={item.value} path={`${path}.item.${index}.value`} /></span>
                <span className={cn(styles.statLabel, 'fdoc-stat-label')}><HighlightedField text={item.label} path={`${path}.item.${index}.label`} /></span>
              </div>
            ))}
          </div>
        </section>
      );
    case 'tabs':
      return expandTabs ? <ExpandedTabs tabs={segment.tabs} path={path} /> : <InteractiveTabs tabs={segment.tabs} path={path} />;
  }
}

function InteractiveTabs({ tabs, path }: { tabs: DocumentTab[]; path: string }) {
  const { t } = useTranslation();
  const [activeIndex, setActiveIndex] = useState(0);
  const readingTabs = useContext(ReadingTabsContext);
  const value = String(Math.min(readingTabs?.selections[path] ?? activeIndex, tabs.length - 1));
  return (
    <TabsPrimitive.Root className={styles.tabs} value={value} onValueChange={next => {
      setActiveIndex(Number(next));
      readingTabs?.onChange?.(path, Number(next));
    }}>
      <TabsPrimitive.List className={styles.tabList} aria-label={t('documents.tabs.label')}>
        {tabs.map((tab, index) => (
          <TabsPrimitive.Trigger
            key={`${tab.label}-${index}`}
            value={String(index)}
            className={styles.tab}
            onPointerMove={pointGlassLight}
            onPointerLeave={resetGlassLight}
          >
            <GlassHoverLens corners={false} />
            <span><HighlightedField text={tab.label} path={`${path}.tab.${index}.label`} /></span>
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {tabs.map((tab, index) => (
        <TabsPrimitive.Content key={`${tab.label}-${index}`} value={String(index)} className={styles.tabPanel}>
          <Segments segments={tab.children} expandTabs={false} path={`${path}.tab.${index}`} />
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

function ExpandedTabs({ tabs, path }: { tabs: DocumentTab[]; path: string }) {
  return (
    <>
      {tabs.map((tab, index) => (
        <section key={`${tab.label}-${index}`} className={cn(styles.tabs, 'fdoc-tab-section')}>
          <p className={cn(styles.expandedTabLabel, 'fdoc-tab-label')}><HighlightedField text={tab.label} path={`${path}.tab.${index}.label`} /></p>
          <div className={cn(styles.tabPanel, 'fdoc-tab-panel')}>
            <Segments segments={tab.children} expandTabs path={`${path}.tab.${index}`} />
          </div>
        </section>
      ))}
    </>
  );
}

/** 富内容字段只标记实际显示字符；无源位置的默认标题保持普通文字。 */
function HighlightedField({ text, path }: { text: string; path: string }) {
  const ranges = useContext(HighlightsContext)?.[path];
  if (!ranges?.length) return text;
  const children = [];
  let offset = 0;
  ranges.forEach(range => {
    if (range.start > offset) children.push(text.slice(offset, range.start));
    children.push(<mark key={range.start} data-document-change={range.kind}>{text.slice(range.start, range.end)}</mark>);
    offset = range.end;
  });
  children.push(text.slice(offset));
  return <>{children}</>;
}

type MarkdownElementProps<T extends keyof React.JSX.IntrinsicElements> = React.ComponentPropsWithoutRef<T> & {
  node?: unknown;
  'data-document-change'?: DocumentHighlightRange['kind'];
};

const DocumentLink = ({ node, ...props }: MarkdownElementProps<'a'>) => {
  void node;
  return <a {...props} target="_blank" rel="noopener noreferrer" />;
};

const DocumentTable = ({ node, ...props }: MarkdownElementProps<'table'>) => {
  void node;
  return (
    <div className={cn(styles.tableWrap, 'fdoc-table-wrap')}>
      <table {...props} />
    </div>
  );
};

function renderDocumentPre({ node, children, ...props }: MarkdownElementProps<'pre'>, staticView: boolean) {
  void node;
  const nodes = React.Children.toArray(children);
  const code = nodes[0];
  if (nodes.length === 1 && React.isValidElement<React.ComponentPropsWithoutRef<'code'>>(code) && code.type === 'code') {
    const language = /language-([^\s]+)/.exec(code.props.className ?? '')?.[1];
    const block = <DocumentCodeBlock language={language} value={String(code.props.children ?? '')} staticView={staticView} />;
    return props['data-document-change'] ? <div data-document-change={props['data-document-change']}>{block}</div> : block;
  }
  return <pre {...props}>{children}</pre>;
}

// renderer 保持模块级稳定，草稿增量更新不重挂代码复制状态。
const DocumentPre = (props: MarkdownElementProps<'pre'>) => renderDocumentPre(props, false);
const StaticDocumentPre = (props: MarkdownElementProps<'pre'>) => renderDocumentPre(props, true);
const DOCUMENT_MARKDOWN_COMPONENTS: Components = { a: DocumentLink, table: DocumentTable, pre: DocumentPre };
const STATIC_MARKDOWN_COMPONENTS: Components = { a: DocumentLink, table: DocumentTable, pre: StaticDocumentPre };

function documentHeading(Tag: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6', path: string): NonNullable<Components['h1']> {
  return function Heading({ node, ...props }) {
    // 位置取自 Markdown 语法树，同名标题和不同标签页仍有稳定且独立的定位键。
    return <Tag {...props} data-document-heading-key={`${path}-${node?.position?.start.offset ?? 0}`} />;
  };
}

const MarkdownBlock = React.memo(function MarkdownBlock({ text, staticView, path }: { text: string; staticView: boolean; path: string }) {
  const highlights = useContext(HighlightsContext)?.[path];
  const highlightPlugins = useMemo(() => highlights?.length ? [createDocumentHighlightPlugin(text, highlights)] : [], [text, highlights]);
  const components = useMemo<Components>(() => ({
    ...DOCUMENT_MARKDOWN_COMPONENTS,
    h1: documentHeading('h1', path), h2: documentHeading('h2', path),
    h3: documentHeading('h3', path), h4: documentHeading('h4', path),
    h5: documentHeading('h5', path), h6: documentHeading('h6', path),
  }), [path]);
  // 文档正文来自模型：不渲染原始 HTML，链接一律新窗口打开。
  return (
    <ReactMarkdown
      remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
      rehypePlugins={highlightPlugins}
      skipHtml
      components={staticView ? STATIC_MARKDOWN_COMPONENTS : components}
    >
      {text}
    </ReactMarkdown>
  );
});
