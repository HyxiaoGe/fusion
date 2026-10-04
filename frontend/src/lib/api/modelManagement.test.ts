import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock('./fetchWithAuth', () => ({
  apiRequest: apiRequestMock,
}));

import {
  fetchModelManagementSnapshotAPI,
  updateModelVisibilityAPI,
} from './modelManagement';

describe('模型管理 API 客户端', () => {
  beforeEach(() => {
    apiRequestMock.mockReset();
  });

  it('读取管理员模型管理快照', async () => {
    apiRequestMock.mockResolvedValue({ models: [] });

    await fetchModelManagementSnapshotAPI();

    expect(apiRequestMock).toHaveBeenCalledWith('/api/admin/model-management');
  });

  it('按 revision 修改模型可见性并保留操作原因', async () => {
    apiRequestMock.mockResolvedValue({});

    await updateModelVisibilityAPI('moonshot/kimi-k3', {
      selectable: false,
      reason: '供应商临时维护',
      expected_revision: 7,
    });

    expect(apiRequestMock).toHaveBeenCalledWith(
      '/api/admin/model-management/models/visibility',
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_id: 'moonshot/kimi-k3',
          selectable: false,
          reason: '供应商临时维护',
          expected_revision: 7,
        }),
      },
    );
  });
});
