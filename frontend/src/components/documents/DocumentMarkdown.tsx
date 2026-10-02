'use client';

import React, { useMemo, useState } from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { Info, Lightbulb, TriangleAlert, Wallet } from 'lucide-react';
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
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import DocumentCodeBlock from './DocumentCodeBlock';
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
      return <MarkdownBlock text={segment.text} staticView={expandTabs} />;
    case 'callout': {
      const Icon = { tip: Lightbulb, info: Info, warning: TriangleAlert, price: Wallet }[segment.variant];
      return (
        <aside className={cn(styles.callout, 'fdoc-callout')} data-variant={segment.variant}>
          <div className={cn(styles.calloutHeading, 'fdoc-callout-heading')}>
            <span className={cn(styles.calloutIcon, 'fdoc-callout-icon')}><Icon aria-hidden="true" /></span>
            <p className={cn(styles.calloutLabel, 'fdoc-callout-label')}>{segment.label ?? t(`documents.callout.${segment.variant}`)}</p>
          </div>
          <div className={cn(styles.calloutBody, 'fdoc-callout-body')}>
            <Segments segments={segment.children} expandTabs={expandTabs} />
          </div>
        </aside>
      );
    }
    case 'timeline':
      return (
        <section className={cn(styles.timeline, 'fdoc-timeline')}>
          {segment.label ? <p className={cn(styles.blockLabel, 'fdoc-block-label')}>{segment.label}</p> : null}
          <Segments segments={segment.children} expandTabs={expandTabs} />
        </section>
      );
    case 'stats':
      if (segment.items.length === 0) return <MarkdownBlock text={segment.fallback} staticView={expandTabs} />;
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
  const { t } = useTranslation();
  const [activeIndex, setActiveIndex] = useState(0);
  const value = String(Math.min(activeIndex, tabs.length - 1));
  return (
    <TabsPrimitive.Root className={styles.tabs} value={value} onValueChange={next => setActiveIndex(Number(next))}>
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
            <span>{tab.label}</span>
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {tabs.map((tab, index) => (
        <TabsPrimitive.Content key={`${tab.label}-${index}`} value={String(index)} className={styles.tabPanel}>
          <Segments segments={tab.children} expandTabs={false} />
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

function ExpandedTabs({ tabs }: { tabs: DocumentTab[] }) {
  return (
    <>
      {tabs.map((tab, index) => (
        <section key={`${tab.label}-${index}`} className={cn(styles.tabs, 'fdoc-tab-section')}>
          <p className={cn(styles.expandedTabLabel, 'fdoc-tab-label')}>{tab.label}</p>
          <div className={cn(styles.tabPanel, 'fdoc-tab-panel')}>
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

function renderDocumentPre({ node, children, ...props }: MarkdownElementProps<'pre'>, staticView: boolean) {
  void node;
  const nodes = React.Children.toArray(children);
  const code = nodes[0];
  if (nodes.length === 1 && React.isValidElement<React.ComponentPropsWithoutRef<'code'>>(code) && code.type === 'code') {
    const language = /language-([^\s]+)/.exec(code.props.className ?? '')?.[1];
    return <DocumentCodeBlock language={language} value={String(code.props.children ?? '')} staticView={staticView} />;
  }
  return <pre {...props}>{children}</pre>;
}

// renderer 保持模块级稳定，草稿增量更新不重挂代码复制状态。
const DocumentPre = (props: MarkdownElementProps<'pre'>) => renderDocumentPre(props, false);
const StaticDocumentPre = (props: MarkdownElementProps<'pre'>) => renderDocumentPre(props, true);
const DOCUMENT_MARKDOWN_COMPONENTS: Components = { a: DocumentLink, table: DocumentTable, pre: DocumentPre };
const STATIC_MARKDOWN_COMPONENTS: Components = { a: DocumentLink, table: DocumentTable, pre: StaticDocumentPre };

const MarkdownBlock = React.memo(function MarkdownBlock({ text, staticView }: { text: string; staticView: boolean }) {
  // 文档正文来自模型：不渲染原始 HTML，链接一律新窗口打开。
  return (
    <ReactMarkdown
      remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
      skipHtml
      components={staticView ? STATIC_MARKDOWN_COMPONENTS : DOCUMENT_MARKDOWN_COMPONENTS}
    >
      {text}
    </ReactMarkdown>
  );
});
