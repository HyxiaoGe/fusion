import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationListItem } from "@/hooks/useConversationList";

const { mockChatItemRender } = vi.hoisted(() => ({
  mockChatItemRender: vi.fn(),
}));

vi.mock("./ChatItem", () => ({
  default: ({ chat, isStreaming }: { chat: ConversationListItem; isStreaming?: boolean }) => {
    mockChatItemRender(chat.id, isStreaming);
    return <div data-testid="chat-item">{chat.title}</div>;
  },
}));

import ChatList from "./ChatList";

describe("ChatList", () => {
  beforeEach(() => {
    mockChatItemRender.mockClear();
  });

  const createStableProps = () => {
    const chats: ConversationListItem[] = [
      {
        id: "chat-a",
        title: "稳定对话",
        model_id: "model-a",
        createdAt: 1_700_000_000_000,
        updatedAt: 1_700_000_000_000,
      },
    ];

    return {
      chats,
      sortedAndGroupedChats: [{ groupLabel: "今天", groupChats: chats }],
      activeChatId: "chat-a",
      streamingConversationIds: [] as string[],
      modelNameById: new Map([["model-a", "测试模型"]]),
      isLoadingServerList: false,
      isLoadingMoreServer: false,
      containerRef: React.createRef<HTMLDivElement>(),
      handleSelectChat: vi.fn(),
      handleStartEditing: vi.fn(),
      handleDeleteChat: vi.fn(),
      handleGenerateTitle: vi.fn(),
      formatDate: vi.fn(() => "06/23/2026"),
      sentinelRef: React.createRef<HTMLDivElement>(),
    };
  };

  it("隐藏对话列表滚动条但保留滚动容器", () => {
    render(<ChatList {...createStableProps()} />);

    expect(screen.getByTestId("chat-list-scroll-container")).toHaveClass("scrollbar-hide");
  });

  it("props 引用保持相同时 rerender 不重复渲染列表项", () => {
    const stableProps = createStableProps();

    const { rerender } = render(<ChatList {...stableProps} />);
    expect(mockChatItemRender).toHaveBeenCalledTimes(1);

    rerender(<ChatList {...stableProps} />);

    expect(mockChatItemRender).toHaveBeenCalledTimes(1);
  });

  it("只把当前流式会话标记为正在输出", () => {
    const stableProps = createStableProps();

    render(<ChatList {...stableProps} streamingConversationIds={['chat-a']} />);

    expect(mockChatItemRender).toHaveBeenCalledWith("chat-a", true);
  });

  it("多个会话同时生成时各自转圈", () => {
    // 此前这里只能收到"那一个"正在生成的会话 ID，因为全局只有一个流槽位；
    // 用户看到的就是同时只有一个转圈。
    const chats: ConversationListItem[] = [
      { id: "chat-a", title: "A", model_id: "model-a", createdAt: 1, updatedAt: 1 },
      { id: "chat-b", title: "B", model_id: "model-a", createdAt: 2, updatedAt: 2 },
      { id: "chat-c", title: "C", model_id: "model-a", createdAt: 3, updatedAt: 3 },
    ];

    render(
      <ChatList
        {...createStableProps()}
        chats={chats}
        sortedAndGroupedChats={[{ groupLabel: "今天", groupChats: chats }]}
        streamingConversationIds={["chat-a", "chat-b"]}
      />,
    );

    expect(mockChatItemRender).toHaveBeenCalledWith("chat-a", true);
    expect(mockChatItemRender).toHaveBeenCalledWith("chat-b", true);
    expect(mockChatItemRender).toHaveBeenCalledWith("chat-c", false);
  });

  it("首屏列表加载中不错误显示暂无对话记录", () => {
    const stableProps = createStableProps();

    render(
      <ChatList
        {...stableProps}
        chats={[]}
        sortedAndGroupedChats={[]}
        isLoadingServerList
      />,
    );

    expect(screen.getByText("加载中...")).toBeInTheDocument();
    expect(screen.queryByText("暂无对话记录")).not.toBeInTheDocument();
  });
});
