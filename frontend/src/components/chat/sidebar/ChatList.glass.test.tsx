import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import React from "react";
import ChatList from "./ChatList";
import type { ConversationListItem } from "@/hooks/useConversationList";

const chats: ConversationListItem[] = [
  { id: "a", title: "对话 A", model_id: "", createdAt: 1, updatedAt: 1 },
  { id: "b", title: "对话 B", model_id: "", createdAt: 2, updatedAt: 2 },
];

const rect = (top: number, height: number) => ({
  x: 0, y: top, left: 0, top, right: 280, bottom: top + height,
  width: 280, height, toJSON: () => ({}),
}) as DOMRect;

describe("ChatList 共享玻璃镜片", () => {
  afterEach(() => vi.restoreAllMocks());

  it("跟随悬停与滚动，离开后回到选中项，点击仍调用原选择逻辑", () => {
    let secondTop = 100;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.dataset.conversationId === "a") return rect(20, 67);
      if (this.dataset.conversationId === "b") return rect(secondTop, 67);
      return rect(0, 300);
    });
    const onSelectChat = vi.fn();
    const handleScroll = vi.fn();
    const containerRef = React.createRef<HTMLDivElement>();

    render(
      <ChatList
        chats={chats}
        sortedAndGroupedChats={[{ groupLabel: "今天", groupChats: chats }]}
        activeChatId="a"
        modelNameById={new Map()}
        isLoadingServerList={false}
        isLoadingMoreServer={false}
        containerRef={containerRef}
        handleScroll={handleScroll}
        handleSelectChat={onSelectChat}
        handleStartEditing={vi.fn()}
        handleDeleteChat={vi.fn()}
        handleGenerateTitle={vi.fn()}
        formatDate={() => "今天"}
      />,
    );

    const lens = screen.getByTestId("chat-glass-lens");
    const container = screen.getByTestId("chat-list-scroll-container");
    const rowB = screen.getByText("对话 B").closest<HTMLElement>("[data-conversation-id]");
    expect(lens.style.getPropertyValue("--lens-y")).toBe("20px");

    fireEvent.pointerMove(rowB!, { pointerType: "mouse", clientX: 90, clientY: 120 });
    expect(lens.style.getPropertyValue("--lens-y")).toBe("100px");
    expect(lens.style.getPropertyValue("--glint-x")).toBe("90px");

    secondTop = 140;
    const originalElementFromPoint = Object.getOwnPropertyDescriptor(document, "elementFromPoint");
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: () => rowB });
    fireEvent.scroll(container);
    if (originalElementFromPoint) {
      Object.defineProperty(document, "elementFromPoint", originalElementFromPoint);
    } else {
      Reflect.deleteProperty(document, "elementFromPoint");
    }
    expect(handleScroll).toHaveBeenCalledOnce();
    expect(lens.style.getPropertyValue("--lens-y")).toBe("140px");

    fireEvent.pointerLeave(container, { pointerType: "mouse" });
    expect(lens.style.getPropertyValue("--lens-y")).toBe("20px");
    fireEvent.click(rowB!);
    expect(onSelectChat).toHaveBeenCalledWith("b");
  });
});
