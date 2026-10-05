import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { AssistantActivity } from './assistantActivity';
import AssistantActivityStatus from './AssistantActivityStatus';

function baseActivity(overrides: Partial<AssistantActivity>): AssistantActivity {
  return {
    kind: 'completed',
    tool: null,
    issue: null,
    searchBlock: null,
    urlBlocks: [],
    hasText: false,
    hasThinking: false,
    shouldSuppressReasoning: false,
    shouldShowSources: false,
    suggestionState: 'idle',
    ...overrides,
  };
}

describe('AssistantActivityStatus', () => {
  it('renders preparing state', () => {
    render(<AssistantActivityStatus activity={baseActivity({ kind: 'preparing' })} />);

    expect(screen.getByText('正在准备回答')).toBeTruthy();
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveAttribute('aria-atomic', 'true');
  });

  it('模型阶段统一按配置表渲染，并带进行中指示', () => {
    const { rerender } = render(<AssistantActivityStatus activity={baseActivity({ kind: 'reasoning', shouldSuppressReasoning: true })} />);
    expect(screen.getByText('正在深度思考')).toBeTruthy();
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'info');

    rerender(<AssistantActivityStatus activity={baseActivity({ kind: 'analyzing' })} />);
    expect(screen.getByText('正在分析结果')).toBeTruthy();
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'neutral');
  });

  it('思考块可见或停止待确认时不展示模型阶段', () => {
    const { container, rerender } = render(
      <AssistantActivityStatus activity={baseActivity({ kind: 'reasoning' })} reasoningVisible />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(<AssistantActivityStatus activity={baseActivity({ kind: 'preparing' })} stopPending />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders running web search with query', () => {
    render(
      <AssistantActivityStatus
        activity={baseActivity({
          kind: 'tool_running',
          tool: {
            kind: 'web_search',
            toolName: 'web_search',
            label: '正在搜索',
            target: 'AI 异常检测',
            call: {
              toolCallId: 'tool-1',
              toolName: 'web_search',
              arguments: { query: 'AI 异常检测' },
              status: 'running',
              startedAt: 1,
            },
          },
        })}
      />,
    );

    expect(screen.getByText('正在搜索')).toBeTruthy();
    expect(screen.getByText('AI 异常检测')).toHaveClass('min-w-0', 'truncate');
    const status = screen.getByRole('status');
    // 色调来自工具注册表
    expect(status).toHaveAttribute('data-tone', 'info');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveAttribute('aria-atomic', 'true');
  });

  it('工具未提供目标时仍展示可理解的运行标签', () => {
    render(
      <AssistantActivityStatus
        activity={baseActivity({
          kind: 'tool_running',
          tool: {
            kind: 'web_search',
            toolName: 'web_search',
            label: '正在搜索',
            target: '',
            call: {
              toolCallId: 'tool-1',
              toolName: 'web_search',
              arguments: { query: 'AI 异常检测' },
              status: 'running',
              startedAt: 1,
            },
          },
        })}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('正在搜索');
    expect(screen.getByRole('status')).not.toHaveTextContent('null');
  });

  it('renders running url read with hostname', () => {
    render(
      <AssistantActivityStatus
        activity={baseActivity({
          kind: 'tool_running',
          tool: {
            kind: 'url_read',
            toolName: 'url_read',
            label: '正在读取网页',
            target: 'example.com',
            call: {
              toolCallId: 'tool-1',
              toolName: 'url_read',
              arguments: { url: 'https://example.com/path' },
              status: 'running',
              startedAt: 1,
            },
          },
        })}
      />,
    );

    expect(screen.getByText('正在读取网页')).toBeTruthy();
    expect(screen.getByText('example.com')).toBeTruthy();
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'teal');
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('renders failed state as alert', () => {
    render(<AssistantActivityStatus activity={baseActivity({ kind: 'failed' })} />);

    expect(screen.getByRole('alert')).toHaveTextContent('生成失败请重试');
    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('aria-live', 'assertive');
    expect(alert).toHaveAttribute('aria-atomic', 'true');
    expect(alert).toHaveAttribute('data-tone', 'danger');
  });

  it('renders interrupted state', () => {
    render(<AssistantActivityStatus activity={baseActivity({ kind: 'interrupted' })} />);

    expect(screen.getByText('生成已停止')).toBeTruthy();
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('renders degraded search issue after completion', () => {
    render(
      <AssistantActivityStatus
        activity={baseActivity({
          kind: 'completed',
          issue: {
            kind: 'degraded',
            toolKind: 'web_search',
            toolName: 'web_search',
            title: '部分搜索结果未能使用',
            detail: '已基于可用信息回答',
            call: {
              toolCallId: 'tool-1',
              toolName: 'web_search',
              arguments: { query: 'AI 新闻' },
              status: 'degraded',
              startedAt: 1,
            },
          },
        })}
      />,
    );

    expect(screen.getByText('部分搜索结果未能使用')).toBeTruthy();
    expect(screen.getByText('已基于可用信息回答')).toBeTruthy();
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('prioritizes failed state over issue copy', () => {
    render(
      <AssistantActivityStatus
        activity={baseActivity({
          kind: 'failed',
          issue: {
            kind: 'failed',
            toolKind: 'web_search',
            toolName: 'web_search',
            title: '搜索未取得可用结果',
            detail: '本轮回答未使用搜索结果',
            call: {
              toolCallId: 'tool-1',
              toolName: 'web_search',
              arguments: { query: 'AI 新闻' },
              status: 'failed',
              startedAt: 1,
            },
          },
        })}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('生成失败');
    expect(screen.queryByText('搜索未取得可用结果')).toBeNull();
    expect(screen.queryByText('本轮回答未使用搜索结果')).toBeNull();
  });

  it('renders nothing for normal completed state without issue', () => {
    const { container } = render(<AssistantActivityStatus activity={baseActivity({ kind: 'completed' })} />);

    expect(container.innerHTML).toBe('');
  });
});
