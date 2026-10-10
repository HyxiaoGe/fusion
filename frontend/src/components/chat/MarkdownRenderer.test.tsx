import React from 'react';
import { act, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, it, expect, vi } from 'vitest';
import i18n from '@/lib/i18n';
import { render, screen, fireEvent, within } from '@testing-library/react';
import MarkdownRenderer from './MarkdownRenderer';

const reactMarkdownRenderMock = vi.hoisted(() => vi.fn());

vi.mock('react-markdown', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-markdown')>();

  return {
    ...actual,
    default: (props: React.ComponentProps<typeof actual.default>) => {
      reactMarkdownRenderMock();
      const ActualReactMarkdown = actual.default;
      return <ActualReactMarkdown {...props} />;
    },
  };
});

// mock CodeBlock，避免拉入 syntax-highlighter 重量依赖
vi.mock('./CodeBlock', () => ({
  default: ({ value }: { value: string }) => <pre data-testid="code-block">{value}</pre>,
}));

const mockSources = [
  { url: 'https://a.com', title: 'Source A', favicon: '' },
  { url: 'https://b.com', title: 'Source B', favicon: '' },
];
const manySources = Array.from({ length: 60 }, (_, index) => ({
  url: `https://example.com/${index + 1}`,
  title: `Source ${index + 1}`,
  favicon: '',
}));

beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
afterEach(() => { vi.restoreAllMocks(); });

beforeEach(() => {
  reactMarkdownRenderMock.mockClear();
});

describe('MarkdownRenderer — citation 行为（contract §9）', () => {
  it('文本里的 [1] 渲染为可点击 chip（button 包含数字 1）', () => {
    render(
      <MarkdownRenderer
        content="Hello [1] world"
        sources={mockSources}
        onCitationClick={vi.fn()}
      />
    );
    // chip 是 button，aria-label 含"参考资料 1"
    const chip = screen.getByRole('button', { name: /参考资料 1/ });
    expect(chip).toBeTruthy();
    expect(chip.textContent).toBe('1');
  });

  it('点击 [1] chip 触发 onCitationClick(0)（0-based）', () => {
    const onCite = vi.fn();
    render(
      <MarkdownRenderer
        content="See [1]"
        sources={mockSources}
        onCitationClick={onCite}
      />
    );
    const chip = screen.getByRole('button', { name: /参考资料 1/ });
    fireEvent.click(chip);
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(onCite).toHaveBeenCalledWith(0);
  });

  // [99] 不在 sources 范围：preprocessCitations 仍会把它转成 ⟦99⟧ 占位符，
  // 但 renderWithCitations 在 source 缺失时回退到 `[99]` 字面量（参见 MarkdownRenderer.tsx:97）。
  // 这是"先处理后还原"的两步行为，不是"未经处理"。
  it('未在 sources 范围内的 [99] 保留原文不转 chip', () => {
    const { container } = render(
      <MarkdownRenderer
        content="Foo [99] bar"
        sources={mockSources}
        onCitationClick={vi.fn()}
      />
    );
    // 原文 [99] 应出现在 DOM 文本中
    expect(container.textContent).toContain('[99]');
    // 不应渲染 button
    const buttons = container.querySelectorAll('button');
    expect(buttons.length).toBe(0);
  });

  it('行内代码块内的 `[1]` 不转 chip 且原文保留', () => {
    const { container } = render(
      <MarkdownRenderer
        content="Use `[1]` syntax"
        sources={mockSources}
        onCitationClick={vi.fn()}
      />
    );
    // preprocessCitations 跳过 backtick 包裹的 inline code，[1] 不应被替换为 ⟦1⟧
    const codeEl = container.querySelector('code');
    expect(codeEl).toBeTruthy();
    expect(codeEl!.textContent).toContain('[1]');
    // code 内不应渲染 citation button
    expect(codeEl!.querySelector('button')).toBeNull();
  });

  it('onCitationClick=undefined 时渲染为 <a href> 而非 button', () => {
    const { container } = render(
      <MarkdownRenderer
        content="See [1]"
        sources={mockSources}
      />
      // 不传 onCitationClick
    );
    // 应渲染 anchor 而不是 button（<a> 的 accessible name 是文本内容 "1"）
    const link = screen.getByRole('link', { name: /^1$/ });
    expect(link).toBeTruthy();
    expect(link.getAttribute('href')).toBe('https://a.com');
    // 不应有 button
    expect(container.querySelector('button')).toBeNull();
  });

  it('连续 [1][2] 都渲染为可点击 chip', () => {
    const onCite = vi.fn();
    render(
      <MarkdownRenderer
        content="Sources: [1][2] both cited"
        sources={mockSources}
        onCitationClick={onCite}
      />
    );
    // 两个 chip 都存在
    const chip1 = screen.getByRole('button', { name: /参考资料 1/ });
    const chip2 = screen.getByRole('button', { name: /参考资料 2/ });
    expect(chip1).toBeTruthy();
    expect(chip2).toBeTruthy();
    // 点击两次分别回调 0 / 1
    fireEvent.click(chip1);
    fireEvent.click(chip2);
    expect(onCite).toHaveBeenNthCalledWith(1, 0);
    expect(onCite).toHaveBeenNthCalledWith(2, 1);
  });

  it('优先按 citation_index 映射乱序且有缺口的来源并回传真实侧边栏索引', () => {
    const onCite = vi.fn();
    render(
      <MarkdownRenderer
        content="Third [3], first [1], missing [2]"
        sources={[
          {
            url: 'https://three.example.com',
            title: '第三来源',
            evidence_id: 'ev-three',
            citation_index: 3,
          },
          {
            url: 'https://one.example.com',
            title: '第一来源',
            evidence_id: 'ev-one',
            citation_index: 1,
          },
        ]}
        onCitationClick={onCite}
      />
    );

    const third = screen.getByRole('button', { name: /参考资料 3：第三来源/ });
    const first = screen.getByRole('button', { name: /参考资料 1：第一来源/ });
    fireEvent.click(third);
    fireEvent.click(first);

    expect(onCite).toHaveBeenNthCalledWith(1, 0);
    expect(onCite).toHaveBeenNthCalledWith(2, 1);
    expect(screen.queryByRole('button', { name: /参考资料 2/ })).toBeNull();
    expect(screen.getByText(/missing \[2\]/)).toBeTruthy();
  });

  it('知识库引用按统一编号打开侧栏且不生成空外链', () => {
    const onCite = vi.fn();
    const { container } = render(
      <MarkdownRenderer
        content="参见安装手册[1]。"
        sources={[{
          title: '安装手册.md',
          url: '',
          evidence_id: 'knowledge-1',
          citation_index: 1,
          kind: 'knowledge',
        }]}
        onCitationClick={onCite}
      />,
    );

    const citation = screen.getByRole('button', { name: '查看知识库参考资料 1：安装手册.md' });
    fireEvent.click(citation);
    expect(onCite).toHaveBeenCalledWith(0);
    expect(container.querySelector('a[href=""]')).toBeNull();
    expect(citation).not.toHaveAttribute('title');
    expect(citation.className).toContain('knowledgeChip');
    expect(citation.querySelector('svg')).not.toBeNull();
  });

  it('网页引用保持原样式，不带知识库标记', () => {
    render(
      <MarkdownRenderer
        content="见官网[1]。"
        sources={[{ title: '官网', url: 'https://example.com', citation_index: 1, kind: 'web' }]}
        onCitationClick={vi.fn()}
      />,
    );

    const citation = screen.getByRole('button', { name: '查看参考资料 1：官网' });
    expect(citation.className).not.toContain('knowledgeChip');
    expect(citation.querySelector('svg')).toBeNull();
  });

  it('h2 标题内的 [1] 也渲染为 chip（processChildren 覆盖标题）', () => {
    render(
      <MarkdownRenderer
        content="## Heading with [1] citation"
        sources={mockSources}
        onCitationClick={vi.fn()}
      />
    );
    // h2 内应该有 chip
    const heading = screen.getByRole('heading', { level: 2 });
    expect(heading).toBeTruthy();
    const chip = within(heading).getByRole('button', { name: /参考资料 1/ });
    expect(chip).toBeTruthy();
  });

  it('表格表头和单元格引用按稳定编号定位来源，缺失编号及代码保持原文', () => {
    const onCite = vi.fn();
    const { container } = render(
      <MarkdownRenderer
        content={'| 参数与架构 [42] | 开源情况 |\n| --- | --- |\n| 552B MoE，输入激活 8B / 输出激活 16B[42] | 开源[55]，待核对[99]，代码 `[42]` |'}
        sources={[
          { title: '开源公告', url: 'https://example.com/open', citation_index: 55 },
          { title: '架构说明', url: 'https://example.com/architecture', citation_index: 42 },
        ]}
        onCitationClick={onCite}
      />,
    );

    const header = screen.getByRole('columnheader', { name: /参数与架构/ });
    fireEvent.click(within(header).getByRole('button', { name: /参考资料 42/ }));
    const cells = screen.getAllByRole('cell');
    fireEvent.click(within(cells[0]).getByRole('button', { name: /参考资料 42/ }));
    fireEvent.click(within(cells[1]).getByRole('button', { name: /参考资料 55/ }));

    expect(onCite.mock.calls).toEqual([[1], [1], [0]]);
    expect(cells[1].textContent).toContain('[99]');
    expect(cells[1].querySelector('code')?.textContent).toBe('[42]');
    expect(container.textContent).not.toMatch(/[⟦⟧]/);
  });

  it('sources=[] 时 [1] 文本原样保留且无 chip', () => {
    const { container } = render(
      <MarkdownRenderer
        content="Empty: [1] no chip"
        sources={[]}
        onCitationClick={vi.fn()}
      />
    );
    // [1] 应该原样在文本里
    expect(container.textContent).toContain('[1]');
    // 没有 button / link / cite chip
    expect(container.querySelector('button')).toBeNull();
    expect(container.querySelector('a[aria-label*="参考资料"]')).toBeNull();
  });

  it('大量引用 chip 不挂载 Radix tooltip trigger，避免 Popper 批量更新', () => {
    const content = manySources.map((_, index) => `[${index + 1}]`).join(' ');
    const { container } = render(
      <MarkdownRenderer
        content={content}
        sources={manySources}
        onCitationClick={vi.fn()}
      />
    );

    expect(screen.getAllByRole('button')).toHaveLength(60);
    expect(container.querySelector('[data-slot="tooltip-trigger"]')).toBeNull();
    expect(document.querySelector('[data-radix-popper-content-wrapper]')).toBeNull();
  });

  it('聚焦或悬停预览不重解析正文、不重挂代码或引用，来源更新沿原节点继续', async () => {
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(1024);
    vi.spyOn(document.documentElement, 'clientHeight', 'get').mockReturnValue(768);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 40, y: 40, top: 40, left: 40, right: 58, bottom: 58, width: 18, height: 18, toJSON: () => ({}) });
    const sources = [{ title: '来源甲', url: 'https://a.example.com', citation_index: 42, snippet: '已有来源摘要' }];
    const { rerender } = render(<MarkdownRenderer content={'回答[42]\n\n```js\nconst a = 1;\n```'} sources={sources} onCitationClick={vi.fn()} />);
    const chip = screen.getByRole('button', { name: /参考资料 42/ });
    const code = screen.getByTestId('code-block');
    act(() => chip.focus());
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('已有来源摘要'));
    expect(reactMarkdownRenderMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('code-block')).toBe(code);
    expect(screen.getByRole('button', { name: /参考资料 42/ })).toBe(chip);
    rerender(<MarkdownRenderer content={'回答[42]\n\n```js\nconst a = 1;\nconst b = 2;\n```'} sources={[{ ...sources[0], snippet: '更新后的摘要' }]} onCitationClick={vi.fn()} />);
    expect(screen.getByRole('tooltip')).toHaveTextContent('更新后的摘要');
    expect(screen.getByTestId('code-block')).toBe(code);
    expect(screen.getByTestId('code-block')).toHaveTextContent('const b = 2;');
  });
});

describe('MarkdownRenderer — code/table 行为', () => {
  it('温度和数值范围中的单个波浪线保持为普通文本', () => {
    const { container } = render(
      <MarkdownRenderer
        content="周六 22~29℃，周日 22~31℃。"
        sources={[]}
      />
    );

    expect(container.textContent).toContain('周六 22~29℃，周日 22~31℃。');
    expect(container.querySelector('del')).toBeNull();
  });

  it('裸 URL 后接中文说明时链接边界停在 URL 本身', () => {
    const { container } = render(
      <MarkdownRenderer
        content="参考 https://example.com/a?froms=ggmp，原因是需要核验。"
        sources={[]}
      />
    );

    const link = screen.getByRole('link', { name: 'https://example.com/a?froms=ggmp' });
    expect(link.getAttribute('href')).toBe('https://example.com/a?froms=ggmp');
    expect(container.textContent).toContain('，原因是需要核验。');
  });

  it('已有 Markdown 链接不被裸 URL 预处理破坏', () => {
    render(
      <MarkdownRenderer
        content="[官网](https://example.com/a?froms=ggmp)，原因是需要核验。"
        sources={[]}
      />
    );

    const link = screen.getByRole('link', { name: '官网' });
    expect(link.getAttribute('href')).toBe('https://example.com/a?froms=ggmp');
  });

  it('多行 fenced code 使用 CodeBlock 渲染', () => {
    render(
      <MarkdownRenderer
        content={'```ts\nconst a = 1;\nconst b = 2;\n```'}
        sources={mockSources}
      />
    );

    expect(screen.getByTestId('code-block').textContent).toBe('const a = 1;\nconst b = 2;');
  });

  it('单行 fenced code 在首个流式片段就使用 CodeBlock 渲染', () => {
    render(
      <MarkdownRenderer
        content={'```ts\nconst a = 1;\n```'}
        sources={[]}
      />
    );

    expect(screen.getByTestId('code-block').textContent).toBe('const a = 1;');
  });

  it('流式增量更新 fenced code 时保留同一个 CodeBlock 实例', () => {
    const { rerender } = render(
      <MarkdownRenderer
        content={'```ts\nconst a = 1;\nconst b = 2;\n```'}
        sources={[]}
      />
    );
    const firstCodeBlock = screen.getByTestId('code-block');

    rerender(
      <MarkdownRenderer
        content={'```ts\nconst a = 1;\nconst b = 2;\nconst c = 3;\n```'}
        sources={[]}
      />
    );

    expect(screen.getByTestId('code-block').textContent).toBe('const a = 1;\nconst b = 2;\nconst c = 3;');
    expect(screen.getByTestId('code-block')).toBe(firstCodeBlock);
  });

  it('列表项内 fenced code 流式增量和引用更新时不重挂 CodeBlock', () => {
    const firstCitationClick = vi.fn();
    const nextCitationClick = vi.fn();
    const initialContent = '- 参考 [1]\n\n  ```ts\n  const a = 1;\n  const b = 2;\n  ```';
    const nextContent = '- 参考 [1]\n\n  ```ts\n  const a = 1;\n  const b = 2;\n  const c = 3;\n  ```';
    const { rerender } = render(
      <MarkdownRenderer
        content={initialContent}
        sources={mockSources}
        onCitationClick={firstCitationClick}
      />
    );
    const firstCodeBlock = screen.getByTestId('code-block');

    fireEvent.click(screen.getByRole('button', { name: /参考资料 1/ }));
    expect(firstCitationClick).toHaveBeenCalledWith(0);

    const nextSources = [
      { url: 'https://updated.example.com', title: 'Updated Source', favicon: '' },
      mockSources[1],
    ];
    rerender(
      <MarkdownRenderer
        content={nextContent}
        sources={nextSources}
        onCitationClick={nextCitationClick}
      />
    );

    expect(screen.getByTestId('code-block')).toBe(firstCodeBlock);
    expect(screen.getByTestId('code-block').textContent).toContain('const c = 3;');
    fireEvent.click(screen.getByRole('button', { name: /Updated Source/ }));
    expect(nextCitationClick).toHaveBeenCalledWith(0);
    expect(firstCitationClick).toHaveBeenCalledTimes(1);
  });

  it('table 保留横向滚动容器和表头、单元格语义', () => {
    const { container } = render(
      <MarkdownRenderer
        content={'| A | B |\n| - | - |\n| 1 | 2 |'}
        sources={mockSources}
      />
    );

    expect(container.querySelector('.overflow-x-auto table')).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'A' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '1' })).toBeInTheDocument();
  });
});

describe('MarkdownRenderer — memo 行为', () => {
  it('相同 props rerender 时不重复执行 ReactMarkdown', () => {
    const sources = [{ title: 'A', url: 'https://example.com/a', favicon: '' }];
    const onCitationClick = vi.fn();

    const { rerender } = render(
      <MarkdownRenderer content="hello [1]" sources={sources} onCitationClick={onCitationClick} />
    );

    rerender(
      <MarkdownRenderer content="hello [1]" sources={sources} onCitationClick={onCitationClick} />
    );

    expect(reactMarkdownRenderMock).toHaveBeenCalledTimes(1);
  });
});
