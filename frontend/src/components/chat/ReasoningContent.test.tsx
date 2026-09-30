import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ReasoningContent from './ReasoningContent';

vi.mock('./CodeBlock', () => ({
  default: ({ language, value }: { language: string; value: string }) => (
    <pre data-testid="reasoning-code-block" data-language={language}>{value}</pre>
  ),
}));

describe('ReasoningContent', () => {
  it('折叠时隐藏辅助阅读和键盘内容，展开后沿用同一内容节点', () => {
    const props = {
      content: '核对 [参考页面](https://example.com/source)',
      isStreaming: false,
      onToggle: vi.fn(),
    };
    const { container, rerender } = render(<ReasoningContent {...props} isVisible={false} />);
    const toggle = screen.getByRole('button', { name: '已深度思考' });
    const link = container.querySelector('a');
    const bodyId = toggle.getAttribute('aria-controls');

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(bodyId).toBeTruthy();
    expect(container.querySelector(`[id="${bodyId}"]`)).toHaveAttribute('inert');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();

    rerender(<ReasoningContent {...props} isVisible={true} />);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(toggle).toHaveAttribute('aria-controls', bodyId);
    expect(container.querySelector(`[id="${bodyId}"]`)).not.toHaveAttribute('inert');
    expect(screen.getByRole('link', { name: '参考页面' })).toBe(link);
  });

  it('完成态保留折叠回调与耗时文案', () => {
    const onToggle = vi.fn();
    render(
      <ReasoningContent
        content="已经完成的思考"
        isStreaming={false}
        isVisible={false}
        onToggle={onToggle}
        duration="1.2"
      />,
    );

    expect(screen.getByText('已深度思考（用时 1.2 秒）')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button'));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('流式态保持内容可读并显示展开状态', () => {
    render(
      <ReasoningContent
        content="正在推理 [参考页面](https://example.com/source)"
        isStreaming={true}
        isVisible={false}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByText('正在深度思考...')).toBeInTheDocument();
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: '参考页面' })).toHaveAttribute('href', 'https://example.com/source');
  });

  it('裸 URL 后接中文说明时不把说明吞进链接', () => {
    const { container } = render(
      <ReasoningContent
        content="读取 https://example.com/a?froms=ggmp，原因是需要核验。"
        isStreaming={false}
        isVisible={true}
        onToggle={vi.fn()}
      />,
    );

    const link = screen.getByRole('link', { name: 'https://example.com/a?froms=ggmp' });
    expect(link.getAttribute('href')).toBe('https://example.com/a?froms=ggmp');
    expect(container.textContent).toContain('，原因是需要核验。');
  });

  it('流式 reasoning 的 fenced code 增量更新时不重挂代码块', () => {
    const { rerender } = render(
      <ReasoningContent
        content={'```ts\nconst a = 1;\nconst b = 2;\n```'}
        isStreaming={true}
        isVisible={true}
        onToggle={vi.fn()}
      />,
    );
    const firstCodeBlock = screen.getByTestId('reasoning-code-block');

    rerender(
      <ReasoningContent
        content={'```ts\nconst a = 1;\nconst b = 2;\nconst c = 3;\n```'}
        isStreaming={true}
        isVisible={true}
        onToggle={vi.fn()}
      />,
    );

    expect(firstCodeBlock).toBeTruthy();
    expect(screen.getByTestId('reasoning-code-block')).toBe(firstCodeBlock);
    expect(screen.getByTestId('reasoning-code-block').textContent).toContain('const c = 3;');
  });

  it('将工具协议标记显示为普通文本，不创建未知 DOM 标签', () => {
    const { container } = render(
      <ReasoningContent
        content={'准备 <strong>核对</strong> 工具调用：\n<function name="web_search">\n<parameter name="query">深圳天气</parameter>\n</function>'}
        isStreaming={false}
        isVisible={true}
        onToggle={vi.fn()}
      />,
    );

    expect(container.querySelector('function')).toBeNull();
    expect(container.querySelector('parameter')).toBeNull();
    expect(container.textContent).toContain('<function name="web_search">');
    expect(container.textContent).toContain('<parameter name="query">深圳天气</parameter>');
    expect(screen.getByText('核对').tagName).toBe('STRONG');
  });

  it('不改写 fenced code 中的工具协议示例', () => {
    render(
      <ReasoningContent
        content={'```xml\n<function>\n<parameter name="query">深圳天气</parameter>\n</function>\n```'}
        isStreaming={false}
        isVisible={true}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.getByTestId('reasoning-code-block').textContent).toBe(
      '<function>\n<parameter name="query">深圳天气</parameter>\n</function>',
    );
  });
});
