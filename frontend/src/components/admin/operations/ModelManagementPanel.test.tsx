import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/types/api';
import type { ModelManagementSnapshot } from '@/types/modelManagement';

const {
  dispatchMock,
  fetchModelManagementSnapshotMock,
  refreshModelsMock,
  updateModelsMock,
  updateModelVisibilityMock,
  updateProvidersMock,
} = vi.hoisted(() => ({
  dispatchMock: vi.fn(),
  fetchModelManagementSnapshotMock: vi.fn(),
  refreshModelsMock: vi.fn(),
  updateModelsMock: vi.fn((payload) => ({ type: 'models/updateModels', payload })),
  updateModelVisibilityMock: vi.fn(),
  updateProvidersMock: vi.fn((payload) => ({ type: 'models/updateProviders', payload })),
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => dispatchMock,
}));

vi.mock('@/redux/slices/modelsSlice', () => ({
  updateModels: updateModelsMock,
  updateProviders: updateProvidersMock,
}));

vi.mock('@/lib/config/modelConfig', () => ({
  refreshModels: refreshModelsMock,
}));

vi.mock('@/lib/api/modelManagement', () => ({
  fetchModelManagementSnapshotAPI: fetchModelManagementSnapshotMock,
  updateModelVisibilityAPI: updateModelVisibilityMock,
}));

import ModelManagementPanel from './ModelManagementPanel';

const baseSnapshot: ModelManagementSnapshot = {
  generated_at: '2026-08-04T08:00:00+08:00',
  models: [
    {
      model_id: 'kimi-k3',
      name: 'Kimi K3',
      provider: 'moonshot',
      provider_display: 'Moonshot',
      health: { status: 'healthy' },
      selectable: true,
      routable: true,
      state: 'active',
      revision: 7,
    },
    {
      model_id: 'legacy-model',
      name: 'Legacy Model',
      provider: 'legacy',
      provider_display: 'Legacy',
      health: { status: 'healthy' },
      selectable: false,
      routable: true,
      state: 'hidden',
      revision: 3,
      reason: '仅保留已有对话',
    },
    {
      model_id: 'gemini-next',
      name: 'Gemini Next',
      provider: 'google',
      provider_display: 'Google Gemini',
      health: { status: 'healthy' },
      selectable: true,
      routable: true,
      state: 'active',
      revision: 2,
    },
  ],
};

function prepareLoadedSnapshot(snapshot = baseSnapshot) {
  fetchModelManagementSnapshotMock.mockResolvedValue(snapshot);
}

async function renderLoaded(snapshot = baseSnapshot) {
  prepareLoadedSnapshot(snapshot);
  render(<ModelManagementPanel />);
  await screen.findByTestId('registered-model-count');
}

describe('ModelManagementPanel', () => {
  beforeEach(() => {
    vi.useRealTimers();
    dispatchMock.mockReset();
    fetchModelManagementSnapshotMock.mockReset();
    refreshModelsMock.mockReset();
    updateModelsMock.mockClear();
    updateModelVisibilityMock.mockReset();
    updateProvidersMock.mockClear();
  });

  it.each(['取消', 'Escape'])('%s 模型恢复确认后回到实际行按钮且不提交请求', async (closeAction) => {
    const user = userEvent.setup();
    await renderLoaded();
    const opener = screen.getByRole('button', { name: '恢复 Legacy Model' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('textbox', { name: '操作原因' })).toHaveFocus();
    await user.keyboard('仅检查交互');
    if (closeAction === '取消') {
      await user.click(within(dialog).getByRole('button', { name: '取消' }));
    } else {
      await user.keyboard('{Escape}');
    }
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
    expect(updateModelVisibilityMock).not.toHaveBeenCalled();
    expect(refreshModelsMock).not.toHaveBeenCalled();
  });

  it('展示统计与已注册模型，不再有治理候选或上线入口', async () => {
    await renderLoaded();

    expect(screen.getByTestId('registered-model-count')).toHaveTextContent('3');
    expect(screen.getByTestId('selectable-model-count')).toHaveTextContent('2');
    expect(screen.getByText('Kimi K3')).toBeInTheDocument();
    expect(screen.getByText('Legacy Model')).toBeInTheDocument();
    expect(screen.queryByText('治理候选')).toBeNull();
    expect(screen.queryByRole('button', { name: /上线|删除/ })).toBeNull();
  });

  it('新对话可选择统计排除不可路由和健康异常的已注册模型', async () => {
    await renderLoaded({
      ...baseSnapshot,
      models: [
        ...baseSnapshot.models,
        {
          model_id: 'route-disabled-model',
          name: 'Route Disabled Model',
          provider: 'legacy',
          provider_display: 'Legacy',
          health: { status: 'healthy' },
          selectable: true,
          routable: false,
          state: 'registered',
          revision: 1,
        },
        {
          model_id: 'unhealthy-model',
          name: 'Unhealthy Model',
          provider: 'legacy',
          provider_display: 'Legacy',
          health: { status: 'unhealthy' },
          selectable: true,
          routable: true,
          state: 'registered',
          revision: 1,
        },
      ],
    });

    expect(screen.getByTestId('registered-model-count')).toHaveTextContent('5');
    expect(screen.getByTestId('selectable-model-count')).toHaveTextContent('2');
  });

  it('按提供商汇总数量并筛选已注册模型', async () => {
    await renderLoaded();

    const providerFilter = screen.getByRole('combobox', { name: '按提供商筛选模型' });
    expect(providerFilter).toHaveTextContent('全部提供商');
    fireEvent.click(providerFilter);

    const googleOption = screen.getByRole('option', { name: /Google Gemini/ });
    expect(googleOption).toHaveTextContent('1 已注册');
    fireEvent.click(googleOption);

    expect(providerFilter).toHaveTextContent('Google Gemini');
    expect(screen.getByText('Gemini Next')).toBeInTheDocument();
    expect(screen.queryByText('Kimi K3')).toBeNull();
    expect(screen.queryByText('Legacy Model')).toBeNull();
    expect(screen.getByTestId('visible-registered-model-count')).toHaveTextContent('1 / 3');
  });

  it('统一搜索模型名称和 ID，并忽略大小写及首尾空格', async () => {
    await renderLoaded();

    const searchInput = screen.getByRole('searchbox', { name: '搜索模型' });
    fireEvent.change(searchInput, { target: { value: '  kImI  ' } });

    expect(screen.getByText('Kimi K3')).toBeInTheDocument();
    expect(screen.queryByText('Legacy Model')).toBeNull();
    expect(screen.queryByText('Gemini Next')).toBeNull();
    expect(screen.getByTestId('visible-registered-model-count')).toHaveTextContent('1 / 3');
  });

  it('搜索与提供商分类叠加，支持提供商名称并可一键清空', async () => {
    await renderLoaded();

    const providerFilter = screen.getByRole('combobox', { name: '按提供商筛选模型' });
    fireEvent.click(providerFilter);
    fireEvent.click(screen.getByRole('option', { name: /Google Gemini/ }));

    const searchInput = screen.getByRole('searchbox', { name: '搜索模型' });
    fireEvent.change(searchInput, { target: { value: 'kimi' } });
    expect(screen.getByText('当前提供商没有匹配的已注册模型')).toBeInTheDocument();

    fireEvent.change(searchInput, { target: { value: ' gemini ' } });
    expect(screen.getByText('Gemini Next')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '清除模型搜索' }));
    expect(searchInput).toHaveValue('');
    expect(screen.getByText('Gemini Next')).toBeInTheDocument();
    expect(providerFilter).toHaveTextContent('Google Gemini');
  });

  it('刷新后所选提供商已不存在时自动恢复全部分类', async () => {
    const withoutGoogle: ModelManagementSnapshot = {
      ...baseSnapshot,
      models: baseSnapshot.models.filter((model) => model.provider !== 'google'),
    };
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce(baseSnapshot)
      .mockResolvedValueOnce(withoutGoogle);
    refreshModelsMock.mockResolvedValue({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    const providerFilter = screen.getByRole('combobox', { name: '按提供商筛选模型' });
    fireEvent.click(providerFilter);
    fireEvent.click(screen.getByRole('option', { name: /Google Gemini/ }));
    expect(providerFilter).toHaveTextContent('Google Gemini');

    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => expect(providerFilter).toHaveTextContent('全部提供商'));
    expect(screen.getByText('Kimi K3')).toBeInTheDocument();
    expect(screen.queryByText('Gemini Next')).toBeNull();
  });

  it('隐藏动作要求填写原因并明确只影响新选择，成功后刷新管理快照与全局模型 Redux', async () => {
    const refreshedSnapshot = {
      ...baseSnapshot,
      models: baseSnapshot.models.map((model) => (
        model.model_id === 'kimi-k3' ? { ...model, selectable: false, state: 'hidden', revision: 8 } : model
      )),
    };
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce(baseSnapshot)
      .mockResolvedValueOnce(refreshedSnapshot);
    updateModelVisibilityMock.mockResolvedValue({});
    refreshModelsMock.mockResolvedValue({
      providers: [{ id: 'moonshot', name: 'Moonshot', order: 1 }],
      models: [{
        id: 'kimi-k3',
        name: 'Kimi K3',
        provider: 'moonshot',
        capabilities: {},
        temperature: 0.7,
        enabled: true,
        selectable: false,
        routable: true,
      }],
    });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('仅从新选择中隐藏，已有对话仍可用');
    const confirmButton = within(dialog).getByRole('button', { name: '确认隐藏' });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('操作原因'), {
      target: { value: '供应商临时维护' },
    });
    expect(confirmButton).not.toBeDisabled();
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(updateModelVisibilityMock).toHaveBeenCalledWith('kimi-k3', {
        selectable: false,
        reason: '供应商临时维护',
        expected_revision: 7,
      });
    });
    await waitFor(() => expect(fetchModelManagementSnapshotMock).toHaveBeenCalledTimes(2));
    expect(refreshModelsMock).toHaveBeenCalledTimes(1);
    expect(updateProvidersMock).toHaveBeenCalledWith([{ id: 'moonshot', name: 'Moonshot', order: 1 }]);
    expect(updateModelsMock).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'kimi-k3', selectable: false, enabled: true, routable: true }),
    ]);
    expect(dispatchMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'models/updateProviders' }));
    expect(dispatchMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'models/updateModels' }));
  });

  it('不可路由的隐藏模型不能恢复到新对话选择器', async () => {
    await renderLoaded({
      ...baseSnapshot,
      models: [{
        ...baseSnapshot.models[1],
        routable: false,
      }],
    });

    expect(screen.getByText('当前模型不可路由，无法恢复到新对话选择器。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '不可恢复 Legacy Model' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '恢复 Legacy Model' })).toBeNull();
  });

  it('健康异常的隐藏模型只恢复显示，不误报为立即可选择', async () => {
    const unhealthyModel = {
      ...baseSnapshot.models[1],
      health: { status: 'unhealthy' as const },
    };
    const refreshedSnapshot = {
      ...baseSnapshot,
      models: [{ ...unhealthyModel, selectable: true, revision: 4 }],
    };
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce({ ...baseSnapshot, models: [unhealthyModel] })
      .mockResolvedValueOnce(refreshedSnapshot);
    updateModelVisibilityMock.mockResolvedValue({});
    refreshModelsMock.mockResolvedValue({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');

    expect(screen.getByText(/可恢复显示，健康恢复后才可用于新对话/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '恢复显示 Legacy Model' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('仅恢复模型选择器中的可见性');
    expect(dialog).toHaveTextContent('健康恢复后才可用于新对话');
    expect(dialog).not.toHaveTextContent('新对话可以再次选择');

    fireEvent.change(within(dialog).getByLabelText('操作原因'), { target: { value: '恢复展示' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '确认恢复' }));

    expect(await screen.findByRole('status')).toHaveTextContent('已恢复显示，健康恢复后才可用于新对话');
  });

  it('可见性已写入但后续刷新失败时不误报写操作失败', async () => {
    prepareLoadedSnapshot();
    updateModelVisibilityMock.mockResolvedValue({});
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce(baseSnapshot)
      .mockRejectedValueOnce(new Error('管理快照刷新失败'));
    refreshModelsMock.mockResolvedValue({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '临时维护' } });
    fireEvent.click(screen.getByRole('button', { name: '确认隐藏' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('可见性已更新，但后续页面或模型目录刷新未完成');
    expect(screen.getByRole('status')).toHaveTextContent('Kimi K3 的可见性已更新');
    expect(screen.queryByText('模型可见性更新失败')).toBeNull();
    expect(updateModelsMock).toHaveBeenCalledWith([]);
    expect(dispatchMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'models/updateModels' }));
  });

  it('可见性目录刷新失败后，手动刷新会重新同步管理快照与模型目录', async () => {
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce(baseSnapshot)
      .mockResolvedValue(baseSnapshot);
    updateModelVisibilityMock.mockResolvedValue({});
    refreshModelsMock
      .mockRejectedValueOnce(new Error('目录服务暂时不可用'))
      .mockResolvedValueOnce({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '临时维护' } });
    fireEvent.click(screen.getByRole('button', { name: '确认隐藏' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('可见性已更新，但后续页面或模型目录刷新未完成');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => expect(refreshModelsMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('status')).toHaveTextContent('模型管理数据和模型选择器已刷新');
    expect(updateModelsMock).toHaveBeenCalledWith([]);
  });

  it('手动刷新期间锁定写操作，完成后可继续更新可见性', async () => {
    let resolveOldRefresh!: (snapshot: ModelManagementSnapshot) => void;
    const oldRefresh = new Promise<ModelManagementSnapshot>((resolve) => {
      resolveOldRefresh = resolve;
    });
    const refreshedSnapshot: ModelManagementSnapshot = {
      ...baseSnapshot,
      models: baseSnapshot.models.map((model) => (
        model.model_id === 'kimi-k3' ? { ...model, selectable: false, state: 'hidden', revision: 8 } : model
      )),
    };
    fetchModelManagementSnapshotMock
      .mockResolvedValueOnce(baseSnapshot)
      .mockReturnValueOnce(oldRefresh)
      .mockResolvedValueOnce(refreshedSnapshot);
    updateModelVisibilityMock.mockResolvedValue({});
    refreshModelsMock.mockResolvedValue({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    expect(fetchModelManagementSnapshotMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: '刷新' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '隐藏 Kimi K3' })).toBeDisabled();

    resolveOldRefresh(baseSnapshot);
    await act(async () => {
      await oldRefresh;
      await Promise.resolve();
    });
    expect(screen.getByRole('button', { name: '隐藏 Kimi K3' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '临时维护' } });
    fireEvent.click(screen.getByRole('button', { name: '确认隐藏' }));

    expect(await screen.findByRole('button', { name: '恢复 Kimi K3' })).toBeInTheDocument();
    expect(fetchModelManagementSnapshotMock).toHaveBeenCalledTimes(3);
  });

  it('操作失败时保留可见错误', async () => {
    prepareLoadedSnapshot();
    updateModelVisibilityMock.mockRejectedValue(new Error('revision 已变化'));

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '临时隐藏' } });
    fireEvent.click(screen.getByRole('button', { name: '确认隐藏' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('revision 已变化');
  });

  it('403 时安全降级，不泄露管理数据', async () => {
    fetchModelManagementSnapshotMock.mockRejectedValue(
      new ApiError('FORBIDDEN', '只有管理员可以访问', 'request-1'),
    );

    render(<ModelManagementPanel />);

    expect(await screen.findByText('当前账号无权访问模型管理')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('当前账号无权访问模型管理');
    expect(screen.queryByText('已注册模型')).toBeNull();
    expect(screen.queryByRole('button', { name: /上线|隐藏|恢复/ })).toBeNull();
  });

  it('鉴权刷新瞬时失败时保留重试入口，不误判为永久无权限', async () => {
    fetchModelManagementSnapshotMock.mockRejectedValue(
      new ApiError('AUTH_REFRESH_UNAVAILABLE', '登录状态刷新暂时失败，请稍后重试', ''),
    );

    render(<ModelManagementPanel />);

    expect(await screen.findByRole('alert')).toHaveTextContent('登录状态刷新暂时失败');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.queryByText('当前账号无权访问模型管理')).toBeNull();
  });

  it('操作请求进行中时禁用确认与其他管理动作', async () => {
    let resolveAction!: () => void;
    const pendingAction = new Promise<void>((resolve) => {
      resolveAction = resolve;
    });
    prepareLoadedSnapshot();
    updateModelVisibilityMock.mockReturnValue(pendingAction);
    refreshModelsMock.mockResolvedValue({ models: [], providers: [] });

    render(<ModelManagementPanel />);
    await screen.findByTestId('registered-model-count');
    fireEvent.click(screen.getByRole('button', { name: '隐藏 Kimi K3' }));
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '维护' } });
    fireEvent.click(screen.getByRole('button', { name: '确认隐藏' }));

    expect(screen.getByRole('button', { name: '处理中' })).toBeDisabled();
    resolveAction();
    fireEvent.click(document.body);
  });
});
