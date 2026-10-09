import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  activateRuntimeConfigEntryMock,
  createRuntimeConfigEntryMock,
  fetchRuntimeConfigSnapshotMock,
  setRuntimeConfigEntryActiveMock,
  validateRuntimeConfigMock,
} = vi.hoisted(() => ({
  activateRuntimeConfigEntryMock: vi.fn(),
  createRuntimeConfigEntryMock: vi.fn(),
  fetchRuntimeConfigSnapshotMock: vi.fn(),
  setRuntimeConfigEntryActiveMock: vi.fn(),
  validateRuntimeConfigMock: vi.fn(),
}));

vi.mock('@/lib/api/runtimeConfig', () => ({
  activateRuntimeConfigEntryAPI: activateRuntimeConfigEntryMock,
  createRuntimeConfigEntryAPI: createRuntimeConfigEntryMock,
  fetchRuntimeConfigSnapshotAPI: fetchRuntimeConfigSnapshotMock,
  setRuntimeConfigEntryActiveAPI: setRuntimeConfigEntryActiveMock,
  validateRuntimeConfigAPI: validateRuntimeConfigMock,
}));

import RuntimeConfigManager from './RuntimeConfigManager';

const baseSnapshot = {
  generated_at: '2026-07-03T06:00:00Z',
  effective: [
    {
      namespace: 'prompt_template',
      key: 'generate_title',
      source: 'db',
      version: '2026-07-03.good',
      valid: true,
      issues: [],
      skipped_versions: ['2026-07-03.bad'],
      validation_warnings: {
        '2026-07-03.bad': ['template 必须是非空字符串'],
      },
      payload: { template: '有效标题 prompt' },
    },
  ],
  entries: [
    {
      id: 'row-active',
      namespace: 'prompt_template',
      key: 'generate_title',
      version: '2026-07-03.good',
      is_active: true,
      valid: true,
      issues: [],
      description: '当前版本',
      created_at: '2026-07-03T05:00:00Z',
      updated_at: '2026-07-03T05:30:00Z',
      payload: { template: '有效标题 prompt' },
    },
    {
      id: 'row-candidate',
      namespace: 'prompt_template',
      key: 'generate_title',
      version: '2026-07-03.next',
      is_active: false,
      valid: true,
      issues: [],
      description: '候选版本',
      created_at: '2026-07-03T05:40:00Z',
      updated_at: '2026-07-03T05:40:00Z',
      payload: { template: '候选标题 prompt' },
    },
    {
      id: 'row-invalid',
      namespace: 'prompt_template',
      key: 'generate_title',
      version: '2026-07-03.bad',
      is_active: false,
      valid: false,
      issues: ['template 必须是非空字符串'],
      description: '坏版本',
      created_at: '2026-07-03T05:10:00Z',
      updated_at: '2026-07-03T05:10:00Z',
      payload: { template: '' },
    },
  ],
};

function prepareSnapshot(snapshot = baseSnapshot) {
  fetchRuntimeConfigSnapshotMock.mockResolvedValue(snapshot);
}

async function renderLoaded() {
  render(<RuntimeConfigManager />);
  await screen.findByText('当前生效配置');
}

describe('RuntimeConfigManager', () => {
  beforeEach(() => {
    fetchRuntimeConfigSnapshotMock.mockReset();
    validateRuntimeConfigMock.mockReset();
    createRuntimeConfigEntryMock.mockReset();
    activateRuntimeConfigEntryMock.mockReset();
    setRuntimeConfigEntryActiveMock.mockReset();
  });

  it('加载后展示当前 effective 配置和数据库版本列表', async () => {
    prepareSnapshot();

    await renderLoaded();

    expect(screen.getAllByText('标题生成 Prompt').length).toBeGreaterThan(0);
    expect(screen.getAllByText('影响新对话标题自动生成。').length).toBeGreaterThan(0);
    expect(screen.getAllByText('内部标识：prompt_template / generate_title').length).toBeGreaterThan(0);
    expect(screen.getAllByText('模板内容：11 字').length).toBeGreaterThan(0);
    expect(screen.getByText('2026-07-03.good')).toBeInTheDocument();
    expect(screen.getByText('跳过 1 个坏版本')).toBeInTheDocument();
    expect(screen.getAllByText('候选版本').length).toBeGreaterThan(0);
    expect(screen.getByText('2026-07-03.next')).toBeInTheDocument();
    expect(screen.getByText('template 必须是非空字符串')).toBeInTheDocument();
  });

  it('版本记录说明这是只读观察面板', async () => {
    prepareSnapshot();

    await renderLoaded();

    expect(screen.getByText('配置版本记录')).toBeInTheDocument();
    expect(
      screen.getByText('这里只展示运行时配置的当前状态和历史记录；配置变更仍通过代码、Agent 和 CI/CD 流程完成。'),
    ).toBeInTheDocument();
  });

  it('不暴露创建、校验、激活和禁用入口', async () => {
    prepareSnapshot();
    await renderLoaded();

    expect(screen.queryByText('创建候选版本')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('命名空间')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('配置 Key')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('版本号')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('描述')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('JSON Payload')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '校验' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建候选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '激活' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '禁用' })).not.toBeInTheDocument();
    expect(validateRuntimeConfigMock).not.toHaveBeenCalled();
    expect(createRuntimeConfigEntryMock).not.toHaveBeenCalled();
    expect(activateRuntimeConfigEntryMock).not.toHaveBeenCalled();
    expect(setRuntimeConfigEntryActiveMock).not.toHaveBeenCalled();
  });

  it('初始加载和初始失败不展示零条配置，并可只读重试', async () => {
    let reject!: (reason: Error) => void;
    fetchRuntimeConfigSnapshotMock.mockReturnValueOnce(new Promise((_, rejectRequest) => { reject = rejectRequest; }));
    render(<RuntimeConfigManager />);
    expect(screen.getByRole('status')).toHaveTextContent('正在加载运行时配置');
    expect(screen.queryByText('当前配置')).not.toBeInTheDocument();
    await act(async () => reject(new Error('配置查询失败')));
    expect(screen.getByRole('alert')).toHaveTextContent('配置查询失败');
    expect(screen.queryByText('当前配置')).not.toBeInTheDocument();
    prepareSnapshot();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('当前生效配置')).toBeInTheDocument();
    expect(fetchRuntimeConfigSnapshotMock).toHaveBeenCalledTimes(2);
    expect(createRuntimeConfigEntryMock).not.toHaveBeenCalled();
  });

  it('成功的空快照明确区分没有生效配置和没有版本记录', async () => {
    prepareSnapshot({ ...baseSnapshot, effective: [], entries: [] });
    await renderLoaded();
    expect(screen.getByText('暂无生效配置')).toBeInTheDocument();
    expect(screen.getByText('暂无配置版本记录')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('刷新保留原内容与按钮节点，禁用重复请求，完成后更新快照', async () => {
    prepareSnapshot();
    await renderLoaded();
    let resolve!: (value: typeof baseSnapshot) => void;
    fetchRuntimeConfigSnapshotMock.mockReturnValueOnce(new Promise((resolveRequest) => { resolve = resolveRequest; }));
    const refresh = screen.getByRole('button', { name: '刷新运行时配置' });
    fireEvent.click(refresh);
    expect(refresh).toBeDisabled();
    expect(screen.getByText('刷新中')).toBeInTheDocument();
    expect(screen.getByTestId('runtime-config-entry-row-active')).toBeInTheDocument();
    fireEvent.click(refresh);
    expect(fetchRuntimeConfigSnapshotMock).toHaveBeenCalledTimes(2);
    await act(async () => resolve({ ...baseSnapshot, entries: [] }));
    expect(refresh).toBeEnabled();
    expect(refresh).toBe(screen.getByRole('button', { name: '刷新运行时配置' }));
    expect(screen.getByText('暂无配置版本记录')).toBeInTheDocument();
  });

  it('刷新失败保留上次数据并标明旧快照，再次刷新可恢复', async () => {
    prepareSnapshot();
    await renderLoaded();
    fetchRuntimeConfigSnapshotMock.mockRejectedValueOnce(new Error('暂时无法读取配置'));
    fireEvent.click(screen.getByRole('button', { name: '刷新运行时配置' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法读取配置');
    expect(screen.getByRole('alert')).toHaveTextContent('当前显示上次读取的配置，可再次刷新。');
    expect(screen.getByTestId('runtime-config-entry-row-active')).toBeInTheDocument();
    prepareSnapshot();
    fireEvent.click(screen.getByRole('button', { name: '刷新运行时配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '刷新运行时配置' })).toBeEnabled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
