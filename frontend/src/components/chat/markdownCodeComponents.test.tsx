import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ReactMarkdown from 'react-markdown';
import { MarkdownCodeRenderer, MarkdownPreRenderer } from './markdownCodeComponents';
import ReasoningContent from './ReasoningContent';

const markdownComponents = { pre: MarkdownPreRenderer, code: MarkdownCodeRenderer };

describe('Markdown 代码块边界', () => {
  it('没有语言标记的 fenced code 保留代码块、缩进与换行，行内代码独立渲染', () => {
    const { container } = render(
      <ReactMarkdown components={markdownComponents}>
        {'使用 `inline`。\n\n```\n  first line\n\tsecond line\n```'}
      </ReactMarkdown>,
    );

    expect(screen.getByRole('button', { name: '复制代码' })).toBeInTheDocument();
    expect(container.querySelector('pre code')?.textContent).toBe('  first line\n\tsecond line');
    const inlineCode = container.querySelector('p code');
    expect(inlineCode?.textContent).toBe('inline');
    expect(inlineCode?.closest('pre')).toBeNull();
  });

  it('正文无语言长代码默认显示 12 行，展开状态在流式增量中保留', () => {
    const lines = Array.from({ length: 14 }, (_, index) => `line ${index + 1}`);
    const { container, rerender } = render(
      <ReactMarkdown components={markdownComponents}>
        {`\`\`\`\n${lines.join('\n')}\n\`\`\``}
      </ReactMarkdown>,
    );

    expect(screen.getByText('12/14 行')).toBeInTheDocument();
    expect(container.querySelector('pre code')?.textContent).not.toContain('line 13');
    fireEvent.click(screen.getByTitle('展开代码'));
    const firstPre = container.querySelector('pre');

    rerender(
      <ReactMarkdown components={markdownComponents}>
        {`\`\`\`\n${[...lines, 'line 15'].join('\n')}\n\`\`\``}
      </ReactMarkdown>,
    );

    expect(container.querySelector('pre')).toBe(firstPre);
    expect(container.querySelector('pre code')?.textContent).toContain('line 15');
    expect(screen.queryByText(/显示剩余/)).toBeNull();
  });

  it('推理无语言代码沿用 10 行上限且不显示行号', () => {
    const lines = Array.from({ length: 14 }, (_, index) => `line ${index + 1}`);
    const { container } = render(
      <ReasoningContent
        content={`\`\`\`\n${lines.join('\n')}\n\`\`\``}
        isStreaming={false}
        isVisible
        onToggle={() => {}}
      />,
    );

    expect(screen.getByText('显示剩余 4 行代码')).toBeInTheDocument();
    expect(container.querySelector('pre code')?.textContent).not.toContain('line 11');
    expect(screen.queryByText(/\/14 行/)).toBeNull();
  });
});
