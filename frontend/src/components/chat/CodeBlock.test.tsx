import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import CodeBlock from './CodeBlock';

const highlightMock = vi.hoisted(() => vi.fn((value: string) => ({
  value: `<mark>${value}</mark>`,
})));
const highlightAutoMock = vi.hoisted(() => vi.fn((value: string) => ({
  value: `<em>${value}</em>`,
})));
const getLanguageMock = vi.hoisted(() => vi.fn((language: string) => language !== 'unknown'));

vi.mock('highlight.js', () => ({
  default: {
    getLanguage: getLanguageMock,
    highlight: highlightMock,
    highlightAuto: highlightAutoMock,
  },
}));

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  return writeText;
}

describe('CodeBlock', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    highlightMock.mockClear();
    highlightAutoMock.mockClear();
    getLanguageMock.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('首个 HTML commit 已包含高亮代码，不经过空内容占位', () => {
    const html = renderToString(
      <CodeBlock language="ts" value="const answer = 42;" />,
    );

    expect(html).toContain('<mark>const answer = 42;</mark>');
    expect(highlightMock).toHaveBeenCalledTimes(1);
  });

  it('每次流式 value 增量只执行一次高亮', () => {
    const { rerender } = render(
      <CodeBlock language="ts" value={'const a = 1;\nconst b = 2;'} maxLines={12} />,
    );

    expect(highlightMock).toHaveBeenCalledTimes(1);

    rerender(
      <CodeBlock language="ts" value={'const a = 1;\nconst b = 2;\nconst c = 3;'} maxLines={12} />,
    );

    expect(highlightMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText('3 行')).toBeInTheDocument();
  });

  it('长代码默认折叠，只高亮可见行且可以展开', () => {
    render(
      <CodeBlock language="ts" value={'line 1\nline 2\nline 3'} maxLines={2} />,
    );

    expect(screen.getByText('2/3 行')).toBeInTheDocument();
    expect(screen.getByText('显示剩余 1 行代码')).toBeInTheDocument();
    const code = document.querySelector('code.language-ts');
    expect(code?.textContent).toBe('line 1\nline 2');
    expect(code?.textContent).not.toContain('line 3');
    expect(highlightMock).toHaveBeenCalledTimes(1);
    expect(highlightMock).toHaveBeenLastCalledWith('line 1\nline 2', { language: 'typescript' });

    fireEvent.click(screen.getByTitle('展开代码'));

    expect(screen.getByText('3 行')).toBeInTheDocument();
    expect(code?.textContent).toBe('line 1\nline 2\nline 3');
  });

  it('未知语言回退自动检测并同步渲染结果', () => {
    const html = renderToString(
      <CodeBlock language="unknown" value="some code" showLineNumbers={false} />,
    );

    expect(html).toContain('<em>some code</em>');
    expect(highlightAutoMock).toHaveBeenCalledTimes(1);
    expect(highlightMock).not.toHaveBeenCalled();
  });

  it('折叠显示时复制完整代码，而不是仅复制可见行', async () => {
    const writeText = clipboard();
    const value = 'line 1\nline 2\nline 3';
    render(<CodeBlock language="text" value={value} maxLines={2} />);
    expect(document.querySelector('pre code')?.textContent).not.toContain('line 3');
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(value));
    expect(await screen.findByText('已复制')).toBeInTheDocument();
  });

  it('复制待完成时显示状态并禁用重复操作，失败后提示手动复制且可以重试', async () => {
    let rejectCopy!: (error: Error) => void;
    const writeText = clipboard(vi.fn()
      .mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectCopy = reject; }))
      .mockResolvedValue(undefined));
    render(<CodeBlock language="text" value="完整代码" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(screen.getByText('复制中…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制代码' })).toBeDisabled();

    await act(async () => { rejectCopy(new Error('denied')); });
    expect(screen.getByRole('alert')).toHaveTextContent('复制失败，请选择代码手动复制或重试。');
    expect(screen.getByRole('button', { name: '复制代码' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(await screen.findByText('已复制')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  it('内容更新后旧复制完成不显示成功，卸载后的复制回调也不创建计时器', async () => {
    let resolveCopy!: () => void;
    clipboard(vi.fn(() => new Promise<void>(resolve => { resolveCopy = resolve; })));
    const timer = vi.spyOn(globalThis, 'setTimeout');
    const { rerender, unmount } = render(<CodeBlock language="ts" value="旧内容" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    rerender(<CodeBlock language="ts" value="新内容" />);
    await act(async () => { resolveCopy(); });
    expect(screen.queryByText('已复制')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制代码' })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    unmount();
    timer.mockClear();
    await act(async () => { resolveCopy(); });
    expect(timer).not.toHaveBeenCalled();
  });

  it('旧内容复制失败不会给新内容显示错误', async () => {
    let rejectCopy!: (error: Error) => void;
    clipboard(vi.fn(() => new Promise<void>((_, reject) => { rejectCopy = reject; })));
    const { rerender } = render(<CodeBlock language="ts" value="旧内容" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    rerender(<CodeBlock language="ts" value="新内容" />);
    await act(async () => { rejectCopy(new Error('denied')); });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('复制')).toBeInTheDocument();
  });

  it('成功反馈到期恢复，更新和卸载清理已持有的计时器', async () => {
    vi.useFakeTimers();
    clipboard();
    const { rerender, unmount } = render(<CodeBlock language="text" value="内容" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    await act(async () => {});
    expect(screen.getByText('已复制')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByText('复制')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    await act(async () => {});
    expect(vi.getTimerCount()).toBe(1);
    rerender(<CodeBlock language="text" value="增量内容" />);
    expect(screen.getByText('复制')).toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);

    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    await act(async () => {});
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('代码滚动层可以获得键盘焦点，折叠控件指向该区域', () => {
    render(<CodeBlock language="text" value={'第一行\n第二行'} maxLines={1} />);
    const region = screen.getByRole('region', { name: '代码内容：纯文本' });
    expect(region).toHaveAttribute('tabindex', '0');
    region.focus();
    expect(region).toHaveFocus();
    expect(screen.getByRole('button', { name: '展开代码' })).toHaveAttribute('aria-controls', region.id);
  });

  it('英文模式使用翻译后的代码元信息和操作文案', async () => {
    await i18n.changeLanguage('en-US');
    render(<CodeBlock language="text" value={'first\nsecond'} maxLines={1} />);
    expect(screen.getByText('Plain text')).toBeInTheDocument();
    expect(screen.getByText('1/2 lines')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand code' })).toBeInTheDocument();
    expect(screen.getByText('Show 1 remaining line')).toBeInTheDocument();
  });
});
