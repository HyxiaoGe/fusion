import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  mockDispatch,
  mockUsePathname,
  mockSetSelectedModel,
  mockUpdateConversationModel,
  mockAddRecentModel,
  mockGetRecentModels,
  mockState,
} = vi.hoisted(() => ({
  mockDispatch: vi.fn(),
  mockUsePathname: vi.fn(),
  mockSetSelectedModel: vi.fn((payload: string) => ({ type: 'models/setSelectedModel', payload })),
  mockUpdateConversationModel: vi.fn((payload: { id: string; model_id: string }) => ({
    type: 'conversation/updateConversationModel',
    payload,
  })),
  mockAddRecentModel: vi.fn(),
  mockGetRecentModels: vi.fn(() => [] as string[]),
  mockState: { current: {} as any },
}));

vi.mock('next/navigation', () => ({
  usePathname: mockUsePathname,
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => mockDispatch,
  useAppSelector: (selector: (state: any) => unknown) => selector(mockState.current),
}));

const createState = () => ({
  models: {
    selectedModelId: 'model-a',
    loadStatus: 'ready',
    providers: [{ id: 'provider-a', name: 'Provider A', order: 1 }],
    models: [
      {
        id: 'model-a',
        name: '模型 A',
        provider: 'provider-a',
        enabled: true,
        capabilities: { searchCapable: true, agentTools: true, webSearch: true, vision: true, deepThinking: true },
      },
      {
        id: 'model-b',
        name: '模型 B',
        provider: 'provider-a',
        enabled: true,
        capabilities: { searchCapable: false, agentTools: false, functionCalling: true, vision: false, deepThinking: false },
      },
    ],
  },
  conversation: {
    hydrationStatus: { new: 'done' },
    byId: {
      new: {
        id: 'new',
        model_id: 'model-a',
        messages: [{ id: 'message-1', role: 'user' }],
      },
    },
  },
});

vi.mock('@/redux/slices/modelsSlice', () => ({
  setSelectedModel: mockSetSelectedModel,
}));

vi.mock('@/redux/slices/conversationSlice', () => ({
  updateConversationModel: mockUpdateConversationModel,
}));

vi.mock('@/lib/models/recentModels', () => ({
  getRecentModels: mockGetRecentModels,
  addRecentModel: mockAddRecentModel,
}));

vi.mock('./ProviderIcon', () => ({
  default: ({ providerId }: { providerId: string }) => <span aria-hidden="true">{providerId}</span>,
}));

vi.mock('./ModelSelectorPanel', () => ({
  default: ({ onSelect }: { onSelect: (modelId: string) => void }) => (
    <button type="button" onClick={() => onSelect('model-b')}>
      模型 B
    </button>
  ),
}));

import ModelSelector from './ModelSelector';

describe('ModelSelector 路由语义', () => {
  beforeEach(() => {
    mockDispatch.mockClear();
    mockSetSelectedModel.mockClear();
    mockUpdateConversationModel.mockClear();
    mockAddRecentModel.mockClear();
    mockGetRecentModels.mockClear();
    mockGetRecentModels.mockReturnValue([]);
    mockUsePathname.mockReturnValue('/chat/new');
    mockState.current = createState();
  });

  it('在 /chat/new 且 Redux 存在 byId.new.messages 时仍可选择模型且不更新 id 为 new 的会话', () => {
    render(<ModelSelector />);

    const trigger = screen.getByTestId('model-selector-trigger');
    expect(trigger).not.toBeDisabled();
    fireEvent.click(trigger);

    fireEvent.click(screen.getByRole('button', { name: '模型 B' }));

    expect(mockSetSelectedModel).toHaveBeenCalledWith('model-b');
    expect(mockDispatch).toHaveBeenCalledWith({ type: 'models/setSelectedModel', payload: 'model-b' });
    expect(mockUpdateConversationModel).not.toHaveBeenCalled();
    expect(mockDispatch).not.toHaveBeenCalledWith({
      type: 'conversation/updateConversationModel',
      payload: { id: 'new', model_id: 'model-b' },
    });
  });

  it('不在触发按钮上挂原生 title，避免下拉时出现双重提示', () => {
    render(<ModelSelector />);

    const trigger = screen.getByTestId('model-selector-trigger');
    expect(trigger).not.toHaveAttribute('title');
  });

  it('已有消息的会话可查看绑定模型说明，但不展示选择入口或派发模型修改', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = {
      id: 'chat-1', model_id: 'model-a', messages: [{ id: 'message-1', role: 'user' }],
    };
    render(<ModelSelector />);

    const trigger = screen.getByRole('button', { name: '模型 A，查看已绑定模型信息' });
    expect(trigger).not.toBeDisabled();
    expect(trigger).toHaveAttribute('title', '查看已绑定模型信息');
    expect(trigger.querySelector('svg.lucide-lock-keyhole')).not.toBeNull();
    fireEvent.click(trigger);

    expect(screen.getByText('本会话已绑定模型，切换模型请新建对话')).toBeInTheDocument();
    expect(screen.getByTestId('model-selector-bound-info')).toHaveTextContent('模型 A');
    expect(screen.queryByRole('button', { name: '模型 B' })).not.toBeInTheDocument();
    expect(mockDispatch).not.toHaveBeenCalled();
    expect(mockAddRecentModel).not.toHaveBeenCalled();
  });

  it('已有会话还没有用户消息时仍可选择模型并更新该会话', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = { id: 'chat-1', model_id: 'model-a', messages: [] };
    render(<ModelSelector />);

    fireEvent.click(screen.getByTestId('model-selector-trigger'));
    fireEvent.click(screen.getByRole('button', { name: '模型 B' }));

    expect(mockUpdateConversationModel).toHaveBeenCalledWith({ id: 'chat-1', model_id: 'model-b' });
  });

  it('外部 disabled 阻止打开模型说明', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = {
      id: 'chat-1', model_id: 'model-a', messages: [{ id: 'message-1', role: 'user' }],
    };
    render(<ModelSelector disabled />);

    const trigger = screen.getByTestId('model-selector-trigger');
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByTestId('model-selector-bound-info')).not.toBeInTheDocument();
  });

  it('模型加载中时保持不可操作', () => {
    mockState.current.models.loadStatus = 'loading';
    render(<ModelSelector />);

    const trigger = screen.getByRole('button', { name: '模型加载中' });
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByTestId('model-selector-panel')).not.toBeInTheDocument();
  });

  it('已完成水合但没有当前模型的会话保持不可用', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = {
      id: 'chat-1', model_id: null, messages: [{ id: 'message-1', role: 'user' }],
    };
    mockState.current.conversation.hydrationStatus['chat-1'] = 'done';
    render(<ModelSelector />);

    expect(screen.getByRole('button', { name: '模型不可用' })).toBeDisabled();
  });

  it('原绑定模型不在目录时展示自动选择和接续说明，保留原会话绑定', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = {
      id: 'chat-1', model_id: 'retired-model', messages: [{ id: 'message-1', role: 'user' }],
    };
    mockState.current.models.models.push({
      id: 'auto', name: '自动选择', provider: 'auto', enabled: true, capabilities: {},
    });
    render(<ModelSelector />);

    fireEvent.click(screen.getByRole('button', { name: '自动选择，查看已绑定模型信息' }));
    expect(screen.getByText('原绑定模型已下线，后续消息由自动选择接续。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '模型 B' })).not.toBeInTheDocument();
    expect(mockDispatch).not.toHaveBeenCalled();
    expect(mockState.current.conversation.byId['chat-1'].model_id).toBe('retired-model');
  });

  it('选择面板打开后会话新增用户消息，立即改为绑定说明', () => {
    mockUsePathname.mockReturnValue('/chat/chat-1');
    mockState.current.conversation.byId['chat-1'] = { id: 'chat-1', model_id: 'model-a', messages: [] };
    const { rerender } = render(<ModelSelector />);
    fireEvent.click(screen.getByTestId('model-selector-trigger'));
    expect(screen.getByRole('button', { name: '模型 B' })).toBeInTheDocument();

    mockState.current.conversation.byId['chat-1'].messages.push({ id: 'message-1', role: 'user' });
    rerender(<ModelSelector />);

    expect(screen.getByText('本会话已绑定模型，切换模型请新建对话')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '模型 B' })).not.toBeInTheDocument();
    expect(mockDispatch).not.toHaveBeenCalled();
  });
});
