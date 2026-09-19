import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationListItem } from '@/hooks/useConversationList';
import i18n from '@/lib/i18n';

const {
  mockUseConversationList,
  mockUseSidebarActions,
  mockUsePathname,
  mockDispatch,
  mockChatListProps,
  selectorState,
  themeRuntimeState,
} = vi.hoisted(() => ({
  mockUseConversationList: vi.fn(),
  mockUseSidebarActions: vi.fn(),
  mockUsePathname: vi.fn(),
  mockDispatch: vi.fn(),
  mockChatListProps: vi.fn(),
  selectorState: {
    models: { models: [{ id: 'model-a', name: '测试模型' }] },
    theme: { mode: 'system' },
    // 流槽位按会话索引：假 stream 代表"当前那条流"，挂在它自己的会话 ID 下。
    streamState: { isStreaming: false, conversationId: null as string | null },
    get stream() {
      return {
        byConversation: this.streamState.conversationId
          ? { [this.streamState.conversationId]: this.streamState }
          : {},
      };
    },
  },
  themeRuntimeState: {
    resolvedTheme: 'light' as 'light' | 'dark',
    hasMounted: true,
  },
}));

vi.mock('next/navigation', () => ({
  usePathname: mockUsePathname,
}));

vi.mock('@/hooks/useConversationList', () => ({
  useConversationList: mockUseConversationList,
}));

vi.mock('@/hooks/useSidebarActions', () => ({
  useSidebarActions: mockUseSidebarActions,
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => mockDispatch,
  useAppSelector: (selector: (state: any) => unknown) =>
    selector(selectorState),
}));

vi.mock('@/lib/hooks/useResolvedTheme', () => ({
  useResolvedTheme: () => themeRuntimeState.resolvedTheme,
}));

vi.mock('@/hooks/useHasMounted', () => ({
  useHasMounted: () => themeRuntimeState.hasMounted,
}));

vi.mock('@/components/layouts/UserAvatarMenu', () => ({
  UserAvatarMenu: () => <div data-testid="user-avatar-menu" />,
}));

vi.mock('./sidebar/DeleteChatDialog', () => ({
  default: () => <div data-testid="delete-chat-dialog" />,
}));

vi.mock('./sidebar/RenameChatDialog', () => ({
  default: () => <div data-testid="rename-chat-dialog" />,
}));

vi.mock('./sidebar/ChatSidebarHeader', () => ({
  default: ({ onNewChat, isNewChatActive }: { onNewChat: () => void; isNewChatActive?: boolean }) => (
    <button type="button" onClick={onNewChat} aria-pressed={isNewChatActive}>
      新对话
    </button>
  ),
}));

vi.mock('./sidebar/ChatList', () => ({
  default: ({
    chats,
    sortedAndGroupedChats,
    activeChatId,
    containerRef,
    sentinelRef,
    searchQuery,
    streamingConversationIds,
    handleSelectChat,
  }: {
    chats: ConversationListItem[];
    sortedAndGroupedChats: { groupLabel: string; groupChats: ConversationListItem[] }[];
    activeChatId: string | null;
    containerRef: React.RefObject<HTMLDivElement | null>;
    sentinelRef?: React.RefObject<HTMLDivElement | null>;
    searchQuery?: string;
    streamingConversationIds?: readonly string[];
    handleSelectChat?: (id: string) => void;
  }) => {
    mockChatListProps({
      chats,
      sortedAndGroupedChats,
      searchQuery,
      streamingConversationIds,
    });

    return (
      <div ref={containerRef} data-testid="chat-sidebar-scroll-container">
        {chats.map((chat) => (
          <div
            key={chat.id}
            data-active={chat.id === activeChatId ? 'true' : 'false'}
            data-conversation-id={chat.id}
          >
            {chat.title}
            <button
              type="button"
              aria-label={`选择 ${chat.id}`}
              onClick={() => handleSelectChat?.(chat.id)}
            />
          </div>
        ))}
        <div ref={sentinelRef} />
      </div>
    );
  },
}));

import ChatSidebar from './ChatSidebar';

const originalScrollIntoView = Element.prototype.scrollIntoView;

function mockElementRect(element: Element, rect: Pick<DOMRect, 'top' | 'bottom'>) {
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: rect.top,
    width: 240,
    height: rect.bottom - rect.top,
    top: rect.top,
    right: 240,
    bottom: rect.bottom,
    left: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

describe('ChatSidebar', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    vi.useFakeTimers();
    mockUsePathname.mockReturnValue('/chat/chat-a');
    mockUseConversationList.mockReturnValue({
      conversations: [
        {
          id: 'chat-a',
          title: '已激活对话',
          model_id: 'model-a',
          createdAt: 1_700_000_000_000,
          updatedAt: 1_700_000_000_000,
        },
      ],
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults: null,
      isSearching: false,
      searchError: null,
    });
    mockUseSidebarActions.mockReturnValue({
      closeDeleteDialog: vi.fn(),
      closeRenameDialog: vi.fn(),
      confirmDelete: vi.fn(),
      confirmRename: vi.fn(),
      deleteTargetId: null,
      generateTitle: vi.fn(),
      openDeleteDialog: vi.fn(),
      openRenameDialog: vi.fn(),
      renameTargetId: null,
      renameValue: '',
      selectConversation: vi.fn(),
      setRenameValue: vi.fn(),
    });
    mockChatListProps.mockClear();
    selectorState.streamState.isStreaming = false;
    selectorState.streamState.conversationId = null;
    themeRuntimeState.resolvedTheme = 'light';
    themeRuntimeState.hasMounted = true;
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(async () => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    await i18n.changeLanguage('zh-CN');
    if (originalScrollIntoView) {
      Element.prototype.scrollIntoView = originalScrollIntoView;
    } else {
      delete (Element.prototype as Partial<Pick<Element, 'scrollIntoView'>>).scrollIntoView;
    }
  });

  it('active item 已可见时不触发 scrollIntoView', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(<ChatSidebar onNewChat={vi.fn()} activeChatIdOverride="chat-a" />);

    const container = screen.getByTestId('chat-sidebar-scroll-container');
    const activeItem = container.querySelector('[data-conversation-id="chat-a"]');
    expect(activeItem).not.toBeNull();

    mockElementRect(container, { top: 0, bottom: 300 });
    mockElementRect(activeItem as Element, { top: 40, bottom: 80 });

    await vi.advanceTimersByTimeAsync(60);

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('active item 只有亚像素轻微越界时不触发 scrollIntoView', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(<ChatSidebar onNewChat={vi.fn()} activeChatIdOverride="chat-a" />);

    const container = screen.getByTestId('chat-sidebar-scroll-container');
    const activeItem = container.querySelector('[data-conversation-id="chat-a"]');
    expect(activeItem).not.toBeNull();

    mockElementRect(container, { top: 0, bottom: 300 });
    mockElementRect(activeItem as Element, { top: -0.5, bottom: 80 });

    await vi.advanceTimersByTimeAsync(60);

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('active item 部分不可见时仍触发 scrollIntoView', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(<ChatSidebar onNewChat={vi.fn()} activeChatIdOverride="chat-a" />);

    const container = screen.getByTestId('chat-sidebar-scroll-container');
    const activeItem = container.querySelector('[data-conversation-id="chat-a"]');
    expect(activeItem).not.toBeNull();

    mockElementRect(container, { top: 0, bottom: 300 });
    mockElementRect(activeItem as Element, { top: -2, bottom: 80 });

    await vi.advanceTimersByTimeAsync(60);

    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest', behavior: 'smooth' });
  });

  it('搜索模式下保持传给 ChatList 的空分组引用稳定', () => {
    const searchResults: ConversationListItem[] = [
      {
        id: 'chat-search',
        title: '搜索结果',
        model_id: 'model-a',
        createdAt: 1_700_000_000_000,
        updatedAt: 1_700_000_000_000,
      },
    ];

    mockUseConversationList.mockReturnValue({
      conversations: [
        {
          id: 'chat-a',
          title: '普通对话 A',
          model_id: 'model-a',
          createdAt: 1_700_000_000_000,
          updatedAt: 1_700_000_000_000,
        },
      ],
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults,
      isSearching: false,
      searchError: null,
    });

    const { rerender } = render(<ChatSidebar onNewChat={vi.fn()} activeChatIdOverride="chat-a" />);
    const input = screen.getByPlaceholderText('搜索对话...');
    fireEvent.change(input, { target: { value: '搜索' } });

    const firstProps = mockChatListProps.mock.calls.at(-1)?.[0];

    mockUseConversationList.mockReturnValue({
      conversations: [
        {
          id: 'chat-b',
          title: '普通对话 B',
          model_id: 'model-a',
          createdAt: 1_700_000_000_001,
          updatedAt: 1_700_000_000_001,
        },
      ],
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults,
      isSearching: false,
      searchError: null,
    });

    rerender(<ChatSidebar onNewChat={vi.fn()} activeChatIdOverride="chat-a" />);

    const secondProps = mockChatListProps.mock.calls.at(-1)?.[0];
    expect(secondProps.sortedAndGroupedChats).toBe(firstProps.sortedAndGroupedChats);
  });

  it('pathname 是 /chat/new 时不会把 id 为 new 的会话标记为 active', () => {
    mockUsePathname.mockReturnValue('/chat/new');
    mockUseConversationList.mockReturnValue({
      conversations: [
        {
          id: 'new',
          title: '真实 ID 为 new 的会话',
          model_id: 'model-a',
          createdAt: 1_700_000_000_000,
          updatedAt: 1_700_000_000_000,
        },
      ],
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults: null,
      isSearching: false,
      searchError: null,
    });

    render(<ChatSidebar onNewChat={vi.fn()} />);

    expect(screen.getByText('真实 ID 为 new 的会话')).toHaveAttribute('data-active', 'false');
  });

  function twoConversations() {
    return [
      {
        id: 'chat-a',
        title: '已激活对话',
        model_id: 'model-a',
        createdAt: 1_700_000_000_000,
        updatedAt: 1_700_000_000_000,
      },
      {
        id: 'chat-b',
        title: '另一个对话',
        model_id: 'model-a',
        createdAt: 1_700_000_001_000,
        updatedAt: 1_700_000_001_000,
      },
    ];
  }

  function itemOf(container: HTMLElement, id: string) {
    const node = container.querySelector(`[data-conversation-id="${id}"]`);
    if (!node) throw new Error(`找不到会话项 ${id}`);
    return node;
  }

  it('点击对话立即跟手选中，不等路由提交', () => {
    // 选中态此前完全由 pathname 推导。App Router 的 router.push 是 transition：
    // 新路由段渲染完成前旧界面一直留在屏幕上，于是点下去要顿一会儿高亮才动。
    mockUsePathname.mockReturnValue('/chat/chat-a');
    mockUseConversationList.mockReturnValue({
      conversations: twoConversations(),
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults: null,
      isSearching: false,
      searchError: null,
    });

    const { container } = render(<ChatSidebar onNewChat={vi.fn()} />);
    expect(itemOf(container, 'chat-a')).toHaveAttribute('data-active', 'true');

    fireEvent.click(screen.getByRole('button', { name: '选择 chat-b' }));

    // 路由仍停在 chat-a（push 是 mock，不改 pathname），高亮必须已经跟过去
    expect(itemOf(container, 'chat-b')).toHaveAttribute('data-active', 'true');
    expect(itemOf(container, 'chat-a')).toHaveAttribute('data-active', 'false');
  });

  it('路由落定后把选中态交还给路由', () => {
    mockUsePathname.mockReturnValue('/chat/chat-a');
    mockUseConversationList.mockReturnValue({
      conversations: twoConversations(),
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults: null,
      isSearching: false,
      searchError: null,
    });

    const { container, rerender } = render(<ChatSidebar onNewChat={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择 chat-b' }));

    // 导航提交
    mockUsePathname.mockReturnValue('/chat/chat-b');
    rerender(<ChatSidebar onNewChat={vi.fn()} />);
    expect(itemOf(container, 'chat-b')).toHaveAttribute('data-active', 'true');

    // 再由别处（如浏览器后退）改变路由：必须跟着路由走，不能被点击残留卡住
    mockUsePathname.mockReturnValue('/chat/chat-a');
    rerender(<ChatSidebar onNewChat={vi.fn()} />);
    expect(itemOf(container, 'chat-a')).toHaveAttribute('data-active', 'true');
    expect(itemOf(container, 'chat-b')).toHaveAttribute('data-active', 'false');
  });

  it('点完又去了新对话页时不留下错误高亮', () => {
    mockUsePathname.mockReturnValue('/chat/chat-a');
    mockUseConversationList.mockReturnValue({
      conversations: twoConversations(),
      isLoadingList: false,
      isLoadingMore: false,
      loadMore: vi.fn(),
      pagination: null,
      searchConversations: vi.fn(),
      searchResults: null,
      isSearching: false,
      searchError: null,
    });

    const { container, rerender } = render(<ChatSidebar onNewChat={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择 chat-b' }));

    mockUsePathname.mockReturnValue('/chat/new');
    rerender(<ChatSidebar onNewChat={vi.fn()} />);

    expect(itemOf(container, 'chat-a')).toHaveAttribute('data-active', 'false');
    expect(itemOf(container, 'chat-b')).toHaveAttribute('data-active', 'false');
  });

  it('把正在生成的会话 ID 列表传给列表（可以同时有多个）', () => {
    selectorState.streamState.isStreaming = true;
    selectorState.streamState.conversationId = 'chat-a';

    const { rerender } = render(<ChatSidebar onNewChat={vi.fn()} />);

    expect(mockChatListProps.mock.calls.at(-1)?.[0].streamingConversationIds).toEqual(['chat-a']);

    selectorState.streamState.isStreaming = false;
    rerender(<ChatSidebar onNewChat={vi.fn()} />);

    expect(mockChatListProps.mock.calls.at(-1)?.[0].streamingConversationIds).toEqual([]);
  });

  it('hydration 完成前使用稳定的浅色主题按钮，挂载后再同步真实主题', () => {
    themeRuntimeState.resolvedTheme = 'dark';
    themeRuntimeState.hasMounted = false;

    const { rerender } = render(<ChatSidebar onNewChat={vi.fn()} />);

    expect(screen.getByRole('button', { name: '切换到暗色模式' })).toBeTruthy();

    themeRuntimeState.hasMounted = true;
    rerender(<ChatSidebar onNewChat={vi.fn()} />);

    expect(screen.getByRole('button', { name: '切换到亮色模式' })).toBeTruthy();
  });

  it('在主题按钮旁提供语言切换入口', () => {
    render(<ChatSidebar onNewChat={vi.fn()} />);

    const displayControls = screen.getByTestId('sidebar-display-controls');
    expect(within(displayControls).getByRole('button', { name: '切换到英文' })).toHaveTextContent('中');
    expect(within(displayControls).getByRole('button', { name: '切换到暗色模式' })).toBeTruthy();
  });
});
