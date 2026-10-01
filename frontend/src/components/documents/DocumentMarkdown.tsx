'use client';

import React, { useMemo, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { useTranslation } from 'react-i18next';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';
import {
  parseDocumentDirectives,
  type DocumentSegment,
  type DocumentTab,
} from '@/lib/documents/documentDirectives';
import markdownStyles from '@/components/chat/MarkdownRenderer.module.css';
import styles from './DocumentMarkdown.module.css';

interface DocumentMarkdownProps {
  content: string;
  /** 导出静态 HTML 时展开全部 tab，不依赖交互。 */
  expandTabs?: boolean;
  className?: string;
}

export default function DocumentMarkdown({ content, expandTabs = false, className }: DocumentMarkdownProps) {
  const segments = useMemo(() => parseDocumentDirectives(content), [content]);
  return (
    <div className={cn(markdownStyles.content, styles.document, 'fdoc', className)} data-testid="document-markdown">
      <Segments segments={segments} expandTabs={expandTabs} />
    </div>
  );
}

function Segments({ segments, expandTabs }: { segments: DocumentSegment[]; expandTabs: boolean }) {
  return (
    <>
      {segments.map((segment, index) => (
        <SegmentView key={index} segment={segment} expandTabs={expandTabs} />
      ))}
    </>
  );
}

function SegmentView({ segment, expandTabs }: { segment: DocumentSegment; expandTabs: boolean }) {
  const { t } = useTranslation();
  switch (segment.kind) {
    case 'markdown':
      return <MarkdownBlock text={segment.text} />;
    case 'callout':
      return (
        <aside className={cn(styles.callout, 'fdoc-callout')} data-variant={segment.variant}>
          <p className={cn(styles.calloutLabel, 'fdoc-callout-label')}>{segment.label ?? t(`documents.callout.${segment.variant}`)}</p>
          <Segments segments={segment.children} expandTabs={expandTabs} />
        </aside>
      );
    case 'timeline':
      return (
        <section className={cn(styles.timeline, 'fdoc-timeline')}>
          {segment.label ? <p className={cn(styles.blockLabel, 'fdoc-block-label')}>{segment.label}</p> : null}
          <Segments segments={segment.children} expandTabs={expandTabs} />
        </section>
      );
    case 'stats':
      if (segment.items.length === 0) return <MarkdownBlock text={segment.fallback} />;
      return (
        <section className={cn(styles.statsSection, 'fdoc-stats-section')}>
          {segment.label ? <p className={cn(styles.blockLabel, 'fdoc-block-label')}>{segment.label}</p> : null}
          <div className={cn(styles.stats, 'fdoc-stats')}>
            {segment.items.map((item, index) => (
              <div key={`${item.label}-${index}`} className={cn(styles.stat, 'fdoc-stat')}>
                <span className={cn(styles.statValue, 'fdoc-stat-value')}>{item.value}</span>
                <span className={cn(styles.statLabel, 'fdoc-stat-label')}>{item.label}</span>
              </div>
            ))}
          </div>
        </section>
      );
    case 'tabs':
      return expandTabs ? <ExpandedTabs tabs={segment.tabs} /> : <InteractiveTabs tabs={segment.tabs} />;
  }
}

function InteractiveTabs({ tabs }: { tabs: DocumentTab[] }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const active = tabs[Math.min(activeIndex, tabs.length - 1)];
  return (
    <section className={styles.tabs}>
      <div role="tablist" className={styles.tabList}>
        {tabs.map((tab, index) => (
          <button
            key={`${tab.label}-${index}`}
            type="button"
            role="tab"
            aria-selected={tab === active}
            className={styles.tab}
            onClick={() => setActiveIndex(index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className={styles.tabPanel}>
        <Segments segments={active.children} expandTabs={false} />
      </div>
    </section>
  );
}

function ExpandedTabs({ tabs }: { tabs: DocumentTab[] }) {
  return (
    <>
      {tabs.map((tab, index) => (
        <section key={`${tab.label}-${index}`} className={cn(styles.tabs, 'fdoc-tab-section')}>
          <p className={cn(styles.expandedTabLabel, 'fdoc-tab-label')}>{tab.label}</p>
          <div className={styles.tabPanel}>
            <Segments segments={tab.children} expandTabs />
          </div>
        </section>
      ))}
    </>
  );
}

type MarkdownElementProps<T extends keyof React.JSX.IntrinsicElements> = React.ComponentPropsWithoutRef<T> & {
  node?: unknown;
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

const DOCUMENT_MARKDOWN_COMPONENTS: Components = { a: DocumentLink, table: DocumentTable };

const MarkdownBlock = React.memo(function MarkdownBlock({ text }: { text: string }) {
  // 文档正文来自模型：不渲染原始 HTML，链接一律新窗口打开。
  return (
    <ReactMarkdown
      remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
      skipHtml
      components={DOCUMENT_MARKDOWN_COMPONENTS}
    >
      {text}
    </ReactMarkdown>
  );
});
