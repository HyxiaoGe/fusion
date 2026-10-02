import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { AnswerEvidenceSidebarModel } from './answerEvidenceSidebarModel';
import AnswerEvidenceSidebar from './AnswerEvidenceSidebar';

const model: AnswerEvidenceSidebarModel = {
  summary: {
    usedCount: 2,
    candidateCount: 0,
    searchCount: 1,
    urlCount: 1,
    issueCount: 2,
  },
  usedItems: [
    {
      id: 'search-0',
      kind: 'search',
      title: '搜索来源',
      url: 'https://search.example.com/a',
      domain: 'search.example.com',
      favicon: 'https://search.example.com/favicon.ico',
      sourceIndex: 0,
    },
    {
      id: 'url-url-1',
      kind: 'url_read',
      title: '读取来源',
      url: 'https://reader.example.com/a',
      domain: 'reader.example.com',
    },
  ],
  candidateItems: [],
  issueItems: [
    {
      id: 'issue-1',
      kind: 'url_read',
      title: '失败页面',
      url: 'https://failed.example.com',
      domain: 'failed.example.com',
      status: 'failed',
      reason: '网页暂时无法读取',
    },
    {
      id: 'issue-2',
      kind: 'search',
      title: '未使用搜索',
      status: 'degraded',
      reason: '部分搜索结果未能使用',
    },
  ],
  searchQueries: [
    'AI 标准',
    'OpenAI 最新融资',
  ],
  isRenderable: true,
};

beforeEach(async () => { await i18n.changeLanguage('zh-CN'); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('AnswerEvidenceSidebar', () => {
  it('closed 时不渲染侧栏内容', () => {
    const { container } = render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={false}
        onClose={vi.fn()}
      />,
    );

    expect(container.querySelector('[data-testid="answer-evidence-sidebar"]')).toBeNull();
  });

  it('渲染摘要、已使用来源和异常来源', () => {
    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '回答依据' })).toBeInTheDocument();
    expect(screen.getByText('已使用 2 条 · 深读 1 个网页')).toBeInTheDocument();
    expect(screen.getByText('2 个未使用')).toBeInTheDocument();
    expect(screen.getByText('搜索关键词').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('AI 标准')).toBeInTheDocument();
    expect(screen.getByText('OpenAI 最新融资')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '已使用来源' })).toBeInTheDocument();
    expect(screen.getByText('搜索来源')).toBeInTheDocument();
    expect(screen.getByAltText('')).toHaveAttribute('src', 'https://search.example.com/favicon.ico');
    expect(screen.getByText('读取来源')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '未使用来源' })).toBeInTheDocument();
    expect(screen.getByText('失败页面')).toBeInTheDocument();
    expect(screen.getByText('网页暂时无法读取')).toBeInTheDocument();
    expect(screen.getByText('未使用搜索')).toBeInTheDocument();
    expect(screen.getByText('未使用')).toBeInTheDocument();
    expect(screen.getByText('部分可用')).toBeInTheDocument();
  });

  it('回答依据内不渲染联网过程', () => {
    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('network-diagnostics-panel')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '联网过程' })).not.toBeInTheDocument();
  });

  it('打开时使用 dialog 语义并聚焦关闭按钮', () => {
    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog', { name: '回答依据' })).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByTestId('answer-evidence-sidebar')).toHaveClass('max-w-[100vw]');
    expect(screen.getByRole('button', { name: '关闭回答依据' })).toHaveFocus();
  });

  it('点击关闭按钮触发 onClose', () => {
    const onClose = vi.fn();

    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '关闭回答依据' }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('按 ESC 或点击遮罩触发 onClose', () => {
    const onClose = vi.fn();

    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={onClose}
      />,
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByLabelText('关闭回答依据背景'));

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('高亮指定的搜索来源', () => {
    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
        highlightIndex={0}
      />,
    );

    expect(screen.getByTestId('answer-evidence-used-search-0')).toHaveAttribute('data-highlighted', 'true');
  });

  it('highlightTick 变化时重复滚动到同一个搜索来源', () => {
    vi.useFakeTimers();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });

    const { rerender } = render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
        highlightIndex={0}
        highlightTick={1}
      />,
    );

    vi.advanceTimersByTime(100);

    rerender(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
        highlightIndex={0}
        highlightTick={2}
      />,
    );
    vi.advanceTimersByTime(100);

    expect(scrollIntoView).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it('外链按钮带正确 href 和 aria-label', () => {
    render(
      <AnswerEvidenceSidebar
        model={model}
        isOpen={true}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('link', { name: '打开来源：搜索来源' })).toHaveAttribute(
      'href',
      'https://search.example.com/a',
    );
    expect(screen.getByRole('link', { name: '打开来源：读取来源' })).toHaveAttribute(
      'href',
      'https://reader.example.com/a',
    );
  });
});

it('稳定引用编号优先于列表位置，候选也能准确聚焦', () => {
  const sparse = {
    ...model,
    usedItems: [{ ...model.usedItems[0], sourceIndex: 0, citationIndex: 39 }],
    candidateItems: [{ ...model.usedItems[0], id: 'candidate-12', title: '第十二来源', sourceIndex: 11, citationIndex: 12 }],
  };
  render(<AnswerEvidenceSidebar model={sparse} isOpen onClose={() => {}} highlightIndex={0} highlightCitationIndex={12} />);
  expect(screen.getByTestId('answer-evidence-used-search-11')).toHaveAttribute('data-highlighted', 'true');
  expect(screen.getByTestId('answer-evidence-used-search-0')).toHaveAttribute('data-highlighted', 'false');
  expect(screen.getByRole('button', { name: '选择来源 12：第十二来源' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByTestId('answer-evidence-used-search-11')).toHaveAttribute('aria-current', 'true');
  expect(within(screen.getByTestId('answer-evidence-used-search-11')).getByText('12')).toBeVisible();
  for (const sourceIndex of [0, 11]) {
    const card = screen.getByTestId(`answer-evidence-used-search-${sourceIndex}`);
    expect(within(card).getAllByAltText('')).toHaveLength(1);
    expect(within(card).getByAltText('')).toHaveAttribute('src', 'https://search.example.com/favicon.ico');
  }
});

it('键盘选择只改变当前查看来源，不改变依据分组或激活外链', async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  render(<AnswerEvidenceSidebar model={model} isOpen onClose={onClose} />);
  await user.tab();
  expect(screen.getByText('搜索关键词').closest('summary')).toHaveFocus();
  await user.tab();
  const first = screen.getByRole('button', { name: '选择来源：搜索来源' });
  expect(first).toHaveFocus();
  await user.keyboard(' ');
  expect(first).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('当前查看')).toBeVisible();
  await user.click(screen.getByRole('button', { name: '选择来源：读取来源' }));
  expect(first).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('button', { name: '选择来源：读取来源' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getAllByText('当前查看')).toHaveLength(1);
  expect(screen.getByText('已使用 2 条 · 深读 1 个网页')).toBeInTheDocument();
  const link = screen.getByRole('link', { name: '打开来源：搜索来源' });
  expect(link.closest('button')).toBeNull();
  expect(link).toHaveAttribute('target', '_blank');
  expect(onClose).not.toHaveBeenCalled();
});

it('新的正文引用定位优先于侧栏临时选择，关闭重开也不沿用旧选择', () => {
  const sparse = {
    ...model,
    usedItems: [{ ...model.usedItems[0], citationIndex: 39 }],
    candidateItems: [{ ...model.usedItems[0], id: 'candidate-12', title: '候选来源十二', sourceIndex: 11, citationIndex: 12 }],
  };
  const props = { model: sparse, isOpen: true, onClose: vi.fn(), highlightCitationIndex: 12, highlightTick: 1 };
  const { rerender } = render(<AnswerEvidenceSidebar {...props} />);
  fireEvent.click(screen.getByRole('button', { name: '选择来源 39：搜索来源' }));
  expect(screen.getByRole('button', { name: '选择来源 39：搜索来源' })).toHaveAttribute('aria-pressed', 'true');
  rerender(<AnswerEvidenceSidebar {...props} highlightTick={2} />);
  expect(screen.getByRole('button', { name: '选择来源 12：候选来源十二' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '选择来源 39：搜索来源' }));
  rerender(<AnswerEvidenceSidebar {...props} highlightTick={2} isOpen={false} />);
  rerender(<AnswerEvidenceSidebar {...props} highlightTick={2} />);
  expect(screen.getByRole('button', { name: '选择来源 12：候选来源十二' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '选择来源 39：搜索来源' }));
  rerender(<AnswerEvidenceSidebar {...props} highlightTick={2} model={{ ...sparse, usedItems: [{ ...sparse.usedItems[0], url: 'https://new.example.com', title: '替换后的来源' }] }} />);
  expect(screen.getByRole('button', { name: '选择来源 39：替换后的来源' })).toHaveAttribute('aria-pressed', 'false');
  expect(screen.getByRole('button', { name: '选择来源 12：候选来源十二' })).toHaveAttribute('aria-pressed', 'true');
});

it('关键词默认折叠，可展开全部内容，来源顺序保持不变', async () => {
  const user = userEvent.setup();
  render(<AnswerEvidenceSidebar model={model} isOpen onClose={vi.fn()} />);
  const details = screen.getByText('搜索关键词').closest('details')!;
  expect(details).not.toHaveAttribute('open');
  await user.click(screen.getByText('搜索关键词'));
  expect(details).toHaveAttribute('open');
  expect(screen.getByText('OpenAI 最新融资')).toBeVisible();
  await user.click(screen.getByText('搜索关键词'));
  expect(details).not.toHaveAttribute('open');
  expect(screen.getAllByRole('button', { name: /^选择来源：/ }).map(element => element.getAttribute('aria-label'))).toEqual(['选择来源：搜索来源', '选择来源：读取来源']);
});

it('减少动画时直接定位，关闭前取消尚未执行的定位', () => {
  vi.useFakeTimers();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })));
  const scroll = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
  scroll.mockClear();
  const props = { model, isOpen: true, onClose: vi.fn(), highlightIndex: 0 };
  const { rerender } = render(<AnswerEvidenceSidebar {...props} />);
  act(() => { vi.advanceTimersByTime(100); });
  expect(scroll).toHaveBeenCalledWith({ behavior: 'auto', block: 'center' });
  scroll.mockClear();
  fireEvent.click(screen.getByRole('button', { name: '选择来源：读取来源' }));
  rerender(<AnswerEvidenceSidebar {...props} isOpen={false} />);
  act(() => { vi.advanceTimersByTime(100); });
  expect(scroll).not.toHaveBeenCalled();
});

it('新增选中和面板文字随语言切换', async () => {
  await i18n.changeLanguage('en-US');
  render(<AnswerEvidenceSidebar model={model} isOpen onClose={vi.fn()} highlightIndex={0} />);
  expect(screen.getByRole('dialog', { name: 'Answer sources' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Select source: 搜索来源' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('Currently viewing')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Used sources' })).toBeVisible();
  await i18n.changeLanguage('zh-CN');
});
