import i18n from '@/lib/i18n';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import AdminExecutionInspector from './AdminExecutionInspector';

describe('AdminExecutionInspector', () => {
  beforeEach(() => {
    void i18n.changeLanguage('zh-CN');
  });

  it('展示 Agent run、step 和脱敏工具安全投影', () => {
    render(<AdminExecutionInspector
      runs={[{
        id: 'run-1', status: 'completed', model_id: 'gpt-5', answer_model_id: 'gpt-5', provider: 'openai',
        message_id: 'msg-1', turn_index: 3, user_id: 'user-1', total_steps: 1, total_tool_calls: 1, total_duration_ms: 1234, limit_reason: null,
        config: { max_steps: 8 }, error: null, created_at: '2026-07-11T10:00:00Z',
        progress: {
          status: 'running', progress: { phase: 'answering', label: '正在组织回答', completed_steps: 1, total_steps: 1 },
          plan: null, tool_digests: [], evidence: [],
        },
        steps: [{
          id: 'step-1', step_number: 1, status: 'completed', tool_calls_count: 1,
          tool_names: ['step_lookup'], duration_ms: 900, created_at: '2026-07-11T10:00:00Z',
          tool_calls: [{
            id: 'step-tool-1', message_id: 'msg-1', trace_id: 'run-1', step_number: 1,
            tool_name: 'step_lookup', status: 'success', duration_ms: 400,
            arguments: {}, result_preview: {}, error: null, model_id: 'gpt-5', provider: 'openai',
            redacted_fields: [], created_at: '2026-07-11T10:00:00Z',
          }],
        }],
      }]}
      toolCalls={[{
        id: 'tool-1', message_id: 'msg-1', trace_id: 'run-1', step_number: 1, tool_name: 'web_search',
        status: 'success', duration_ms: 800, arguments: { query: 'Fusion' },
        result_preview: { count: 3, api_key: '[REDACTED]' }, error: null,
        model_id: 'gpt-5', provider: 'openai', redacted_fields: ['result_preview.api_key'], created_at: '2026-07-11T10:00:00Z',
      }]}
    />);

    expect(screen.getByText('run-1')).toBeInTheDocument();
    expect(screen.getByText('步骤 1')).toBeInTheDocument();
    expect(screen.getByText('第 3 轮')).toBeInTheDocument();
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0);
    expect(screen.getByText('关联工具：step_lookup（成功）')).toBeInTheDocument();
    expect(screen.getByText('web_search')).toBeInTheDocument();
    expect(screen.getByText(/\[REDACTED\]/)).toBeInTheDocument();
    expect(screen.getByText('运行进度')).toBeInTheDocument();
    expect(screen.queryByText('运行上限配置')).toBeNull();
    expect(screen.queryByText('openai')).toBeNull();
    expect(screen.getByText(/正在组织回答/)).toBeInTheDocument();
  });

  it('普通单步运行只保留汇总，标出备用模型，上限配置只在触发限制时出现', () => {
    const base = {
      status: 'completed', model_id: 'mimo', provider: 'openai', user_id: 'user-1',
      total_steps: 1, total_tool_calls: 0, total_duration_ms: 34000, config: { max_steps: 64 }, error: null,
      created_at: null,
      progress: { run_id: 'run-a', status: 'completed', progress: null, plan: null, tool_digests: [], evidence: [] },
      steps: [{
        id: 'step-a', step_number: 1, status: 'completed', tool_calls_count: 0, tool_names: [],
        duration_ms: 34000, created_at: null, tool_calls: [],
      }],
    };
    render(<AdminExecutionInspector
      runs={[
        { ...base, id: 'run-a', message_id: 'msg-a', turn_index: 15, answer_model_id: 'deepseek-chat', limit_reason: null },
        { ...base, id: 'run-b', message_id: 'msg-b', turn_index: 16, answer_model_id: 'mimo', limit_reason: 'max_steps', status: 'limit_reached' },
      ]}
      toolCalls={[]}
    />);

    expect(screen.getByText('请求 mimo，实际由 deepseek-chat 回答')).toBeInTheDocument();
    expect(screen.queryByText('步骤 1')).toBeNull();
    expect(screen.queryByText('运行进度')).toBeNull();
    expect(screen.getByText('达到步数上限')).toBeInTheDocument();
    expect(screen.getAllByText('运行上限配置')).toHaveLength(1);
  });

  it('空执行记录不渲染误导性容器', () => {
    const { container } = render(<AdminExecutionInspector runs={[]} toolCalls={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
