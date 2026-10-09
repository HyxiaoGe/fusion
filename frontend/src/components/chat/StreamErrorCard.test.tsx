import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const dispatch = vi.hoisted(() => vi.fn());

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => dispatch,
}));

import StreamErrorCard from './StreamErrorCard';
import { openSettingsDialog } from '@/redux/slices/settingsSlice';

describe('StreamErrorCard', () => {
  it('提供商离线时「去管理 Key」打开设置窗口而不是跳转页面', () => {
    render(<StreamErrorCard message="模型暂不可用" code="PROVIDER_OFFLINE" data={{ provider_id: 'qwen' }} />);

    fireEvent.click(screen.getByRole('button', { name: '去管理 Key' }));

    expect(dispatch).toHaveBeenCalledWith(openSettingsDialog({}));
  });

  it('其他错误不显示设置入口', () => {
    render(<StreamErrorCard message="网络错误" code="NETWORK" />);

    expect(screen.queryByRole('button', { name: '去管理 Key' })).not.toBeInTheDocument();
  });
});
