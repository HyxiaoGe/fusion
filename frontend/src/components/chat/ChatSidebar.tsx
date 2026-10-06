"use client";

import React, { useRef, useState, useEffect, useCallback } from "react";
import { usePathname } from "next/navigation";
import { Search, X, Sun, Moon } from "lucide-react";
import { cn } from "@/lib/utils";
import { UserAvatarMenu } from "@/components/layouts/UserAvatarMenu";
import DeleteChatDialog from "./sidebar/DeleteChatDialog";
import RenameChatDialog from "./sidebar/RenameChatDialog";
import ChatSidebarHeader from "./sidebar/ChatSidebarHeader";
import ChatList from "./sidebar/ChatList";
import { useConversationList } from "@/hooks/useConversationList";
import { useSidebarActions } from "@/hooks/useSidebarActions";
import { useConversationActivity } from "@/hooks/useConversationActivity";
import { useAppSelector, useAppDispatch } from "@/redux/hooks";
import { shallowEqual } from "react-redux";
import { selectStreamingConversationIds } from "@/redux/slices/streamSlice";
import { setThemeMode } from "@/redux/slices/themeSlice";
import { useResolvedTheme } from "@/lib/hooks/useResolvedTheme";
import { useHasMounted } from "@/hooks/useHasMounted";
import { getRouteConversationId } from "@/lib/routes/chatRoutes";
import type { ConversationListItem } from "@/hooks/useConversationList";
import { formatInTimeZone } from 'date-fns-tz';
import { LanguageToggle } from "@/components/ui/language-toggle";
import glassSurface from "@/components/ui/GlassSurface.module.css";
import GlassHoverLens, { pointGlassLight, resetGlassLight } from "@/components/ui/GlassHoverLens";

interface ChatSidebarProps {
  onNewChat: () => void;
  activeChatIdOverride?: string | null;
  isNewChatActive?: boolean;
}

const EMPTY_CONVERSATIONS: ConversationListItem[] = [];
const EMPTY_GROUPED_CONVERSATIONS: { groupLabel: string; groupChats: ConversationListItem[] }[] = [];

const ChatSidebar: React.FC<ChatSidebarProps> = ({ onNewChat, activeChatIdOverride, isNewChatActive = false }) => {
  const pathname = usePathname();
  const {
    conversations,
    isLoadingList,
    isLoadingMore,
    loadMore,
    pagination,
    searchConversations,
    searchResults,
    isSearching,
    searchError,
  } = useConversationList();
  const {
    closeDeleteDialog,
    closeRenameDialog,
    confirmDelete,
    confirmRename,
    deleteTargetId,
    generateTitle,
    openDeleteDialog,
    openRenameDialog,
    prefetchConversation,
    renameTargetId,
    renameValue,
    selectConversation,
    setRenameValue,
  } = useSidebarActions();
  const { models } = useAppSelector((state) => state.models);
  const dispatch = useAppDispatch();
  const themeMode = useAppSelector((state) => state.theme.mode);
  // 此前只能给出"那一个"正在生成的会话，因为全局只有一个槽位。
  // 槽位按会话拆开后这是一组，侧边栏可以同时转多个圈。
  const localStreamingConversationIds = useAppSelector(selectStreamingConversationIds, shallowEqual);
  const resolvedTheme = useResolvedTheme(themeMode);
  const hasMounted = useHasMounted();
  // SSR 无法读取 localStorage 里的主题。首个 hydration 帧固定按浅色渲染，
  // 挂载后再同步真实主题，避免服务端 Moon 与客户端 Sun 直接冲突。
  const isDark = hasMounted && resolvedTheme === 'dark';
  const modelNameById = React.useMemo(() => {
    return new Map(models.map((model) => [model.id, model.name]));
  }, [models]);

  const toggleTheme = useCallback(() => {
    dispatch(setThemeMode(isDark ? 'light' : 'dark'));
  }, [dispatch, isDark]);

  const routeConversationId = getRouteConversationId(pathname);
  const routeActiveChatId = activeChatIdOverride === undefined
    ? routeConversationId
    : activeChatIdOverride;
  // 选中态此前完全由 pathname 推导。App Router 的 router.push 是 transition：
  // 新路由段渲染完成前旧界面一直留在屏幕上，于是点下去要顿一会儿高亮才动。
  // 这里把「点了哪个」与「路由到了哪个」解耦：点击当帧就把高亮给出去，
  // 路由落定后再交还给它。内容仍按原来的节奏加载，只是不再挡着反馈。
  const [pendingChatId, setPendingChatId] = useState<string | null>(null);
  const activeChatId = pendingChatId ?? routeActiveChatId;
  // 本页 Redux 只知道本页发起或重连的流；刷新、换标签页后以服务端登记为准。
  const { streamingConversationIds, unreadConversationIds } = useConversationActivity(
    routeActiveChatId,
    localStreamingConversationIds,
  );

  // 路由一旦落定——无论是落到刚点的那个，还是用户去了别处（新对话、后退）——
  // 都把选中态交还给路由，避免点击残留把高亮卡在错的位置。
  useEffect(() => {
    setPendingChatId(null);
  }, [routeConversationId]);

  const handleSelectChat = useCallback((id: string) => {
    setPendingChatId(id);
    selectConversation(id);
  }, [selectConversation]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // active 对话 updatedAt：当 metadata refresh 把它推到顶部时（如刚发完消息），
  // 自动滚到顶部让用户看到位置变化
  const activeUpdatedAt = conversations.find((c) => c.id === activeChatId)?.updatedAt ?? null;

  // activeChatId 变化、退出搜索、或 active 被推到新位置时，滚到当前激活对话
  // 不在搜索模式下生效，避免搜索期间乱跳
  useEffect(() => {
    if (!activeChatId) return;
    if (searchQuery.trim()) return;
    if (!containerRef.current) return;
    // 等下一个渲染帧再 scroll，确保 ChatItem 已渲染（updatedAt 变化触发 sortedAndGroupedChats 重排）
    const timer = setTimeout(() => {
      const container = containerRef.current;
      const target = container?.querySelector(
        `[data-conversation-id="${activeChatId}"]`
      ) as HTMLElement | null;
      if (container && target) {
        const containerRect = container.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const visibilityTolerancePx = 1;
        const isTargetVisible =
          targetRect.top >= containerRect.top - visibilityTolerancePx &&
          targetRect.bottom <= containerRect.bottom + visibilityTolerancePx;
        if (isTargetVisible) return;
        target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [activeChatId, searchQuery, activeUpdatedAt]);

  // sentinel 进视口时自动触发 loadMore，搜索模式下禁用分页加载
  useEffect(() => {
    if (!sentinelRef.current) return;
    if (!pagination?.hasNext) return;
    if (searchQuery.trim()) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !isLoadingMore) {
          void loadMore();
        }
      },
      { rootMargin: '100px' }
    );
    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [pagination?.hasNext, isLoadingMore, loadMore, searchQuery]);

  const formatDate = useCallback((timestamp: number) => {
    const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return formatInTimeZone(new Date(timestamp), timeZone, 'MM/dd/yyyy');
  }, []);

  const getDateGroupLabel = (timestamp: number) => {
    const now = new Date();
    const date = new Date(timestamp);
    const nowDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const chatDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diffDays = Math.floor((nowDate.getTime() - chatDate.getTime()) / (1000 * 60 * 60 * 24));
    if (diffDays <= 0) return "今天";
    if (diffDays === 1) return "昨天";
    if (diffDays <= 3) return "三天内";
    if (diffDays <= 7) return "一周内";
    if (diffDays <= 30) return "一个月内";
    return "更早";
  };

  const sortedAndGroupedChats = React.useMemo(() => {
    const sortedChats = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
    const groups: Record<string, ConversationListItem[]> = {};
    sortedChats.forEach((chat) => {
      const groupLabel = getDateGroupLabel(chat.updatedAt);
      if (!groups[groupLabel]) {
        groups[groupLabel] = [];
      }
      groups[groupLabel].push(chat);
    });
    const groupOrder = ["今天", "昨天", "三天内", "一周内", "一个月内", "更早"];
    return groupOrder
      .map((groupLabel) => ({ groupLabel, groupChats: groups[groupLabel] || [] }))
      .filter((group) => group.groupChats.length > 0);
  }, [conversations]);

  // Cmd/Ctrl+K 聚焦搜索框
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, []);

  // 搜索框 debounce 调后端 API；空 query 立即清空
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchQuery(value);
      if (searchDebounceRef.current) {
        clearTimeout(searchDebounceRef.current);
      }
      if (!value.trim()) {
        void searchConversations('');
        return;
      }
      searchDebounceRef.current = setTimeout(() => {
        void searchConversations(value);
      }, 300);
    },
    [searchConversations]
  );

  useEffect(() => {
    return () => {
      if (searchDebounceRef.current) {
        clearTimeout(searchDebounceRef.current);
      }
    };
  }, []);

  const trimmedSearchQuery = searchQuery.trim();
  const isSearchMode = trimmedSearchQuery.length > 0;
  const displayChats = isSearchMode ? (searchResults ?? EMPTY_CONVERSATIONS) : conversations;
  const handleStartEditing = useCallback((e: React.MouseEvent, chatId: string, currentTitle: string) => {
    e.stopPropagation();
    openRenameDialog(chatId, currentTitle);
  }, [openRenameDialog]);

  const handleDeleteChat = useCallback((e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    openDeleteDialog(chatId);
  }, [openDeleteDialog]);

  const handleGenerateTitle = useCallback((e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    void generateTitle(chatId);
  }, [generateTitle]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ChatSidebarHeader onNewChat={onNewChat} isNewChatActive={isNewChatActive} />

      {/* 搜索框 */}
      <div className="px-3 pb-3">
        <div className={cn(
          "flex h-10 items-center gap-2 rounded-xl px-3 text-sm",
          glassSurface.surface,
          glassSurface.field,
          glassSurface.interactive,
        )} data-focused={isSearchFocused} onPointerMove={pointGlassLight} onPointerLeave={resetGlassLight}>
          <GlassHoverLens />
          <Search className="relative z-10 h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <input
            ref={searchInputRef}
            type="text"
            placeholder="搜索对话..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            onFocus={() => setIsSearchFocused(true)}
            onBlur={() => setIsSearchFocused(false)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                handleSearchChange('');
                searchInputRef.current?.blur();
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => handleSearchChange('')}
              aria-label="清除搜索"
              className="grid h-6 w-6 flex-shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* 搜索状态提示行 */}
      {isSearchMode && isSearching && (
        <div className="px-4 py-1 text-xs text-muted-foreground">搜索中...</div>
      )}
      {isSearchMode && searchError && (
        <div className="px-4 py-1 text-xs text-danger">搜索失败：{searchError}</div>
      )}

      <ChatList
        chats={displayChats}
        sortedAndGroupedChats={isSearchMode ? EMPTY_GROUPED_CONVERSATIONS : sortedAndGroupedChats}
        activeChatId={activeChatId}
        streamingConversationIds={streamingConversationIds}
        unreadConversationIds={unreadConversationIds}
        modelNameById={modelNameById}
        isLoadingServerList={isLoadingList}
        isLoadingMoreServer={isLoadingMore}
        containerRef={containerRef}
        handleSelectChat={handleSelectChat}
        handlePrefetchChat={prefetchConversation}
        searchQuery={trimmedSearchQuery || undefined}
        sentinelRef={sentinelRef}
        handleStartEditing={handleStartEditing}
        handleDeleteChat={handleDeleteChat}
        handleGenerateTitle={handleGenerateTitle}
        formatDate={formatDate}
      />

      {/* 底部用户区（固定不随列表滚动） */}
      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border/70 px-3 pb-3 pt-2">
        <UserAvatarMenu />
        <div className="flex items-center gap-1" data-testid="sidebar-display-controls">
          <LanguageToggle />
          <button
            type="button"
            onClick={toggleTheme}
            className="h-8 w-8 grid place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors duration-fast"
            aria-label={isDark ? '切换到亮色模式' : '切换到暗色模式'}
            title={isDark ? '切换到亮色模式' : '切换到暗色模式'}
          >
            {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </div>

      <DeleteChatDialog
        open={Boolean(deleteTargetId)}
        onOpenChange={(open) => {
          if (!open) closeDeleteDialog();
        }}
        onConfirm={confirmDelete}
      />

      <RenameChatDialog
        open={Boolean(renameTargetId)}
        onOpenChange={(open) => {
          if (!open) closeRenameDialog();
        }}
        onConfirm={() => void confirmRename(renameValue)}
        title={renameValue}
        onTitleChange={setRenameValue}
      />
    </div>
  );
};

export default ChatSidebar;
