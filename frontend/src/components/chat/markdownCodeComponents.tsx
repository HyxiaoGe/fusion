import React from 'react';
import CodeBlock from './CodeBlock';

interface MarkdownCodeProps extends React.ComponentPropsWithoutRef<'code'> {
  node?: unknown;
}

interface MarkdownPreProps extends React.ComponentPropsWithoutRef<'pre'> {
  node?: unknown;
}

function renderMarkdownPre(
  { node, children, ...props }: MarkdownPreProps,
  options: { showLineNumbers: boolean; maxLines: number },
) {
  void node;
  const childNodes = React.Children.toArray(children);
  const code = childNodes[0];

  // 是否为代码块由 pre 结构决定，语言标记只负责选择高亮方式。
  if (childNodes.length === 1 && React.isValidElement<MarkdownCodeProps>(code)
    && (code.type === 'code' || code.type === MarkdownCodeRenderer || code.type === ReasoningCodeRenderer)) {
    const match = /language-([^\s]+)/.exec(code.props.className || '');
    return (
      <CodeBlock
        language={match?.[1] || 'text'}
        value={String(code.props.children).replace(/\n$/, '')}
        showLineNumbers={options.showLineNumbers}
        maxLines={options.maxLines}
      />
    );
  }

  return <pre {...props}>{children}</pre>;
}

/**
 * renderer 必须保持模块级稳定，避免 ReactMarkdown 在流式更新时重挂 CodeBlock。
 */
export const MarkdownPreRenderer = (props: MarkdownPreProps) => renderMarkdownPre(props, {
  showLineNumbers: true,
  maxLines: 12,
});

export const ReasoningPreRenderer = (props: MarkdownPreProps) => renderMarkdownPre(props, {
  showLineNumbers: false,
  maxLines: 10,
});

export const MarkdownCodeRenderer = ({ node, children, ...props }: MarkdownCodeProps) => {
  void node;
  return <code {...props} className="bg-muted px-1 py-0.5 rounded text-sm font-mono">{children}</code>;
};

export const ReasoningCodeRenderer = ({ node, children, ...props }: MarkdownCodeProps) => {
  void node;
  return <code {...props} className="bg-muted px-1 py-0.5 rounded text-xs font-mono">{children}</code>;
};
