"use client";

import React from "react";
import ChatItem from "./ChatItem";
import type { ConversationListItem } from "@/hooks/useConversationList";
import styles from "@/components/ui/GlassLens.module.css";

const EMPTY_STREAMING_IDS: readonly string[] = [];

interface ChatListProps {
  chats: ConversationListItem[];
  sortedAndGroupedChats: { groupLabel: string; groupChats: ConversationListItem[] }[];
  activeChatId: string | null;
  streamingConversationIds?: readonly string[];
  modelNameById: Map<string, string>;
  isLoadingServerList: boolean;
  isLoadingMoreServer: boolean;
  containerRef: React.RefObject<HTMLDivElement | null>;
  handleScroll?: () => void;
  handleSelectChat: (chatId: string) => void;
  handlePrefetchChat?: (chatId: string) => void;
  handleStartEditing: (e: React.MouseEvent, chatId: string, currentTitle: string) => void;
  handleDeleteChat: (e: React.MouseEvent, chatId: string) => void;
  handleGenerateTitle: (e: React.MouseEvent, chatId: string) => void;
  formatDate: (timestamp: number) => string;
  searchQuery?: string;
  sentinelRef?: React.RefObject<HTMLDivElement | null>;
}

const ChatList: React.FC<ChatListProps> = ({
  chats,
  sortedAndGroupedChats,
  activeChatId,
  streamingConversationIds = EMPTY_STREAMING_IDS,
  modelNameById,
  isLoadingServerList,
  isLoadingMoreServer,
  containerRef,
  handleScroll,
  handleSelectChat,
  handlePrefetchChat,
  handleStartEditing,
  handleDeleteChat,
  handleGenerateTitle,
  formatDate,
  searchQuery,
  sentinelRef,
}) => {
  const contentRef = React.useRef<HTMLDivElement>(null);
  const lensRef = React.useRef<HTMLDivElement>(null);
  const hoveredChatIdRef = React.useRef<string | null>(null);
  const lastPointerRef = React.useRef<{ x: number; y: number } | null>(null);
  const instantFrameRef = React.useRef<number | null>(null);

  const rowFromTarget = React.useCallback((target: EventTarget | null): HTMLElement | null => {
    if (!(target instanceof Element)) return null;
    const row = target.closest<HTMLElement>('[data-conversation-id]');
    return row && contentRef.current?.contains(row) ? row : null;
  }, []);

  const rowById = React.useCallback((id: string | null): HTMLElement | null => {
    if (!id || !contentRef.current) return null;
    return Array.from(contentRef.current.querySelectorAll<HTMLElement>('[data-conversation-id]'))
      .find((row) => row.dataset.conversationId === id) ?? null;
  }, []);

  const moveLens = React.useCallback((row: HTMLElement | null, instant = false) => {
    const lens = lensRef.current;
    const content = contentRef.current;
    const container = containerRef.current;
    if (!lens || !content || !container || !row) {
      lens?.classList.remove(styles.visible);
      return;
    }
    const rowRect = row.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    if (rowRect.bottom <= containerRect.top || rowRect.top >= containerRect.bottom) {
      lens.classList.remove(styles.visible);
      return;
    }
    const contentRect = content.getBoundingClientRect();
    const x = rowRect.left - contentRect.left;
    const y = rowRect.top - contentRect.top;
    const previousY = Number(lens.dataset.y);
    const snap = instant || !lens.classList.contains(styles.visible)
      || (Number.isFinite(previousY) && Math.abs(y - previousY) > Math.max(container.clientHeight, 120));
    if (snap) {
      lens.classList.add(styles.instant);
      if (instantFrameRef.current !== null) cancelAnimationFrame(instantFrameRef.current);
      instantFrameRef.current = requestAnimationFrame(() => {
        instantFrameRef.current = requestAnimationFrame(() => {
          lens.classList.remove(styles.instant);
          instantFrameRef.current = null;
        });
      });
    }
    lens.style.setProperty('--lens-x', `${x}px`);
    lens.style.setProperty('--lens-y', `${y}px`);
    lens.style.width = `${rowRect.width}px`;
    lens.style.height = `${rowRect.height}px`;
    lens.dataset.y = String(y);
    lens.classList.add(styles.visible);
  }, [containerRef]);

  const resetLight = React.useCallback(() => {
    const lens = lensRef.current;
    if (!lens) return;
    lens.style.setProperty('--glint-x', '38%');
    lens.style.setProperty('--glint-y', '24%');
    lens.style.setProperty('--corner-tl', '.68');
    lens.style.setProperty('--corner-br', '.68');
  }, []);

  const pointLight = React.useCallback((row: HTMLElement, clientX: number, clientY: number) => {
    const lens = lensRef.current;
    if (!lens) return;
    const rect = row.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
    const diagonal = (x + y) / 2;
    lens.style.setProperty('--glint-x', `${x * rect.width}px`);
    lens.style.setProperty('--glint-y', `${y * rect.height}px`);
    lens.style.setProperty('--corner-tl', (0.48 + 0.42 * (1 - diagonal)).toFixed(2));
    lens.style.setProperty('--corner-br', (0.48 + 0.42 * diagonal).toFixed(2));
  }, []);

  const syncLens = React.useCallback((instant = false) => {
    moveLens(rowById(hoveredChatIdRef.current) ?? rowById(activeChatId), instant);
  }, [activeChatId, moveLens, rowById]);

  React.useLayoutEffect(() => {
    syncLens();
  });

  React.useEffect(() => {
    const container = containerRef.current;
    const content = contentRef.current;
    if (typeof ResizeObserver === 'undefined' || !container || !content) return;
    const observer = new ResizeObserver(() => syncLens(true));
    observer.observe(container);
    observer.observe(content);
    return () => observer.disconnect();
  }, [containerRef, syncLens]);

  React.useEffect(() => {
    const release = () => lensRef.current?.classList.remove(styles.pressed);
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    return () => {
      window.removeEventListener('pointerup', release);
      window.removeEventListener('pointercancel', release);
      if (instantFrameRef.current !== null) cancelAnimationFrame(instantFrameRef.current);
    };
  }, []);

  const rowAtPointer = React.useCallback(() => {
    const pointer = lastPointerRef.current;
    if (!pointer || typeof document.elementFromPoint !== 'function') return null;
    return rowFromTarget(document.elementFromPoint(pointer.x, pointer.y));
  }, [rowFromTarget]);

  return (
    <div
      data-testid="chat-list-scroll-container"
      className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2 scrollbar-hide"
      ref={containerRef}
      onScroll={() => {
        handleScroll?.();
        const row = rowAtPointer();
        hoveredChatIdRef.current = row?.dataset.conversationId ?? null;
        if (row && lastPointerRef.current) {
          pointLight(row, lastPointerRef.current.x, lastPointerRef.current.y);
        } else {
          resetLight();
        }
        syncLens();
      }}
      onPointerMove={(event) => {
        if (event.pointerType === 'touch') return;
        lastPointerRef.current = { x: event.clientX, y: event.clientY };
        const row = rowFromTarget(event.target);
        if (!row) {
          hoveredChatIdRef.current = null;
          resetLight();
          syncLens();
          return;
        }
        if (hoveredChatIdRef.current !== row.dataset.conversationId) {
          hoveredChatIdRef.current = row.dataset.conversationId ?? null;
          moveLens(row);
        }
        pointLight(row, event.clientX, event.clientY);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === 'touch') return;
        hoveredChatIdRef.current = null;
        lastPointerRef.current = null;
        lensRef.current?.classList.remove(styles.pressed);
        resetLight();
        syncLens();
      }}
      onPointerDown={(event) => {
        const row = rowFromTarget(event.target);
        if (!row) return;
        hoveredChatIdRef.current = row.dataset.conversationId ?? null;
        moveLens(row);
        pointLight(row, event.clientX, event.clientY);
        lensRef.current?.classList.add(styles.pressed);
      }}
      onFocusCapture={(event) => {
        const row = rowFromTarget(event.target);
        if (!row) return;
        hoveredChatIdRef.current = row.dataset.conversationId ?? null;
        moveLens(row);
      }}
      onBlurCapture={(event) => {
        if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
        hoveredChatIdRef.current = null;
        resetLight();
        syncLens();
      }}
    >
      <div className={styles.content} ref={contentRef}>
        <div className={styles.lens} ref={lensRef} data-testid="chat-glass-lens" aria-hidden="true">
          <span className={`${styles.corner} ${styles.topLeft}`} />
          <span className={`${styles.corner} ${styles.bottomRight}`} />
        </div>
        {chats.length === 0 && isLoadingServerList ? (
          <div className="p-4 text-center text-muted-foreground text-sm">
            加载中...
          </div>
        ) : chats.length === 0 ? (
          <div className="text-sm text-muted-foreground mt-4 text-center">
            {searchQuery ? `未找到包含 "${searchQuery}" 的对话` : '暂无对话记录'}
          </div>
        ) : searchQuery ? (
          /* 搜索模式：扁平列表，不分组 */
          <div className="space-y-[7px]">
            {chats.map((chat) => (
              <ChatItem
                key={chat.id}
                chat={chat}
                isActive={chat.id === activeChatId}
                isStreaming={streamingConversationIds.includes(chat.id)}
                modelNameById={modelNameById}
                onSelectChat={handleSelectChat}
                onPrefetchChat={handlePrefetchChat}
                onStartEditing={handleStartEditing}
                onDeleteChat={handleDeleteChat}
                onGenerateTitle={handleGenerateTitle}
                formatDate={formatDate}
                searchQuery={searchQuery}
              />
            ))}
          </div>
        ) : (
          /* 正常模式：分组列表 */
          <div>
            {sortedAndGroupedChats.map(({ groupLabel, groupChats }) => (
              <div key={groupLabel} className="mb-3">
                <h3 className="relative z-[1] mb-1 px-3 pt-2 text-xs font-semibold text-muted-foreground">{groupLabel}</h3>
                <div className="space-y-[7px]">
                  {groupChats.map((chat) => (
                    <ChatItem
                      key={chat.id}
                      chat={chat}
                      isActive={chat.id === activeChatId}
                      isStreaming={streamingConversationIds.includes(chat.id)}
                      modelNameById={modelNameById}
                      onSelectChat={handleSelectChat}
                      onPrefetchChat={handlePrefetchChat}
                      onStartEditing={handleStartEditing}
                      onDeleteChat={handleDeleteChat}
                      onGenerateTitle={handleGenerateTitle}
                      formatDate={formatDate}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {isLoadingMoreServer && (
          <div className="p-4 text-center text-muted-foreground text-sm">
            加载更多...
          </div>
        )}

        {sentinelRef && (
          <div ref={sentinelRef} className="h-4" aria-hidden="true" />
        )}
      </div>
    </div>
  );
};

export default React.memo(ChatList);
