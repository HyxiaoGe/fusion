import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMocks = vi.hoisted(() => ({
  getAdminRoutingQuality: vi.fn(),
}));

vi.mock('@/lib/api/adminAudit', () => apiMocks);

import RoutingQualityPanel from './RoutingQualityPanel';

const zeroSignals = {
  classifier_unavailable: 0,
  clarification_only: 0,
  tools_unavailable: 0,
  no_tool_call: 0,
  web_only_fallback: 0,
  primary_tool_missed: 0,
};

const response = {
  scope: {
    created_from: '2026-09-28T10:00:00+08:00',
    created_to: '2026-09-29T10:00:00+08:00',
    unrouted_count: 2,
    running_count: 1,
    interrupted_count: 0,
    failed_count: 0,
    sample_limit: 1,
    sample_total: 3,
  },
  summary: {
    total: 40,
    completed: 39,
    signals: { ...zeroSignals, web_only_fallback: 4, classifier_unavailable: 1 },
  },
  by_package: [
    { package_id: 'weather', total: 30, completed: 30, signals: { ...zeroSignals, web_only_fallback: 4 } },
    { package_id: 'direct', total: 10, completed: 9, signals: zeroSignals },
  ],
  samples: [{
    run_id: 'run-1',
    conversation_id: 'conv-1',
    started_at: '2026-09-29T01:00:00+00:00',
    package_id: 'mcp_explicit',
    status: 'completed',
    signals: ['web_only_fallback'],
    called_tools: ['mcp_abc', 'mcp_def', 'web_search'],
    required_primary_tool_name: null,
  }],
};

const noop = () => undefined;

describe('RoutingQualityPanel', () => {
  beforeEach(() => {
    apiMocks.getAdminRoutingQuality.mockReset().mockResolvedValue(response);
  });

  it('展示信号总览、按能力包统计和可跳转会话的可疑样本', async () => {
    render(<RoutingQualityPanel onForbidden={noop} />);

    const overview = await screen.findByLabelText('路由信号总览');
    expect(overview).toHaveTextContent('只用了联网4');
    expect(overview).toHaveTextContent('分类失败兜底1');
    expect(screen.getByText(/40 次路由，39 个已完成；1 个未结束不计工具类信号；2 个无路由记录/)).toBeInTheDocument();

    const packageRow = screen.getByRole('table', { name: '按能力包统计路由信号' });
    expect(within(packageRow).getAllByRole('row')[1]).toHaveTextContent('weather30 / 30');

    expect(screen.getByText('共 3 条，展示最新 1 条')).toBeInTheDocument();
    const samples = screen.getByRole('table', { name: '可疑路由样本' });
    expect(samples).toHaveTextContent('MCP, web_search');
    expect(within(samples).getByRole('link', { name: '查看会话 conv-1' })).toHaveAttribute(
      'href',
      '/admin?tab=conversations&conversation_id=conv-1',
    );
  });

  it('切换时间窗口按北京时间重新请求', async () => {
    render(<RoutingQualityPanel onForbidden={noop} />);
    await screen.findByLabelText('路由信号总览');

    fireEvent.click(screen.getByRole('button', { name: '路由质量最近 7 天' }));

    await waitFor(() => expect(apiMocks.getAdminRoutingQuality).toHaveBeenCalledTimes(2));
    const [query] = apiMocks.getAdminRoutingQuality.mock.calls[1];
    expect(query.created_from).toMatch(/\+08:00$/);
    const spanMs = Date.parse(query.created_to) - Date.parse(query.created_from);
    expect(spanMs).toBe(7 * 24 * 60 * 60 * 1000);
  });

  it('窗口内没有路由样本时显示空状态', async () => {
    apiMocks.getAdminRoutingQuality.mockResolvedValue({
      ...response,
      summary: { total: 0, completed: 0, signals: zeroSignals },
      by_package: [],
      samples: [],
    });
    render(<RoutingQualityPanel onForbidden={noop} />);

    expect(await screen.findByText('当前时间窗口内暂无带路由结果的运行')).toBeInTheDocument();
  });
});
