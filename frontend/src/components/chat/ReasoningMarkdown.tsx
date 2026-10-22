'use client';

import { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';
import { normalizeBareUrlsForMarkdown } from '@/lib/chat/markdownLinks';
import { ReasoningPreRenderer, ReasoningCodeRenderer } from './markdownCodeComponents';

interface MarkdownSyntaxNode {
  type?: string;
  value?: string;
  children?: MarkdownSyntaxNode[];
}

const TOOL_PROTOCOL_TAG_RE = /<\/?(?:function|functions|function_call|function_calls|tool|tool_call|tool_calls|parameter|parameters|argument|arguments|invoke)\b[^<>]*>/gi;

function escapeHtmlTag(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

/**
 * reasoning 中的工具协议标记不是页面 HTML。只转义 Markdown AST 的原始 HTML 节点，
 * 因此 fenced/inline code 不受影响，其他受支持的原始 HTML 仍交给 rehypeRaw 渲染。
 */
function remarkEscapeToolProtocolTags() {
  return (tree: MarkdownSyntaxNode) => {
    const pending = [tree];
    while (pending.length > 0) {
      const node = pending.pop();
      if (!node) continue;
      if (node.type === 'html' && typeof node.value === 'string') {
        node.value = node.value.replace(TOOL_PROTOCOL_TAG_RE, escapeHtmlTag);
      }
      if (node.children) pending.push(...node.children);
    }
  };
}

export function ReasoningMarkdown({ content }: { content: string }) {
  const rendered = useMemo(() => normalizeBareUrlsForMarkdown(content.trim()), [content]);
  return (
    <ReactMarkdown
      remarkPlugins={[
        remarkEscapeToolProtocolTags,
        [remarkGfm, { singleTilde: false }],
      ]}
      rehypePlugins={[rehypeRaw]}
      components={{
        pre: ReasoningPreRenderer,
        code: ReasoningCodeRenderer,
      }}
    >
      {rendered}
    </ReactMarkdown>
  );
}
