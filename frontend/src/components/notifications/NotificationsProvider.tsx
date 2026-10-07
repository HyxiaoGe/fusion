'use client';

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  getNotifications,
  markAllNotificationsRead,
  type NotificationFilter,
  type NotificationItem,
  type NotificationPage,
} from '@/lib/api/notifications';
import { subscribeNotificationsChanged } from '@/lib/notifications/events';

const PAGE_SIZE = 20;
const POLL_INTERVAL = 15_000;

interface NotificationState {
  sessionKey: string | null;
  filter: NotificationFilter;
  items: NotificationItem[];
  unreadCount: number;
  unreadConversationIds: string[];
  revision: number;
  nextCursor: string | null;
  loaded: boolean;
  loading: boolean;
  error: boolean;
  loadingMore: boolean;
  pageError: boolean;
  readingAll: boolean;
  readError: boolean;
}

function emptyState(sessionKey: string | null, filter: NotificationFilter = 'all'): NotificationState {
  return {
    sessionKey, filter, items: [], unreadCount: 0, unreadConversationIds: [], revision: 0,
    nextCursor: null, loaded: false, loading: false, error: false, loadingMore: false,
    pageError: false, readingAll: false, readError: false,
  };
}

interface NotificationsContextValue extends NotificationState {
  setFilter: (filter: NotificationFilter) => void;
  refresh: () => void;
  loadMore: () => void;
  markAllRead: () => Promise<void>;
}

const NotificationsContext = createContext<NotificationsContextValue>({
  ...emptyState(null), setFilter: () => {}, refresh: () => {}, loadMore: () => {}, markAllRead: async () => {},
});

export function useNotifications(): NotificationsContextValue {
  return useContext(NotificationsContext);
}

export function NotificationsProvider({ sessionKey, children }: {
  sessionKey: string | null;
  children: React.ReactNode;
}) {
  const [state, setState] = useState(() => emptyState(sessionKey));
  const stateRef = useRef(state);
  stateRef.current = state;
  const sessionRef = useRef(sessionKey);
  sessionRef.current = sessionKey;
  const filterRef = useRef<NotificationFilter>('all');
  const generationRef = useRef(0);
  const revisionRef = useRef(0);
  const queryRef = useRef<AbortController | null>(null);
  const pageRef = useRef<AbortController | null>(null);
  const readRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);

  const refresh = useCallback(async (force = false) => {
    const owner = sessionRef.current;
    if (!owner || !mountedRef.current || (queryRef.current && !force)) return;
    queryRef.current?.abort();
    pageRef.current?.abort();
    pageRef.current = null;
    const controller = new AbortController();
    queryRef.current = controller;
    const generation = ++generationRef.current;
    const filter = filterRef.current;
    const oldState = stateRef.current;
    const pagesToLoad = oldState.sessionKey === owner && oldState.filter === filter
      ? Math.max(1, Math.ceil(oldState.items.length / PAGE_SIZE)) : 1;
    const isCurrent = () => mountedRef.current && sessionRef.current === owner
      && generationRef.current === generation && !controller.signal.aborted;
    setState((previous) => ({
      ...(previous.sessionKey === owner && previous.filter === filter ? previous : emptyState(owner, filter)),
      loading: true, error: false, loadingMore: false, pageError: false,
    }));
    try {
      let page = await getNotifications({ filter, limit: PAGE_SIZE }, controller.signal);
      let items = [...page.items];
      let cursor = page.next_cursor;
      // 刷新已展开的页，避免轮询不断把用户送回第一页。版本变化时重取首屏，防止拼接旧已读状态。
      for (let index = 1; index < pagesToLoad && cursor; index += 1) {
        const next = await getNotifications({ filter, cursor, limit: PAGE_SIZE }, controller.signal);
        if (next.revision !== page.revision) {
          page = await getNotifications({ filter, limit: PAGE_SIZE }, controller.signal);
          items = [...page.items];
          cursor = page.next_cursor;
          break;
        }
        items.push(...next.items);
        cursor = next.next_cursor;
      }
      if (!isCurrent() || page.revision < revisionRef.current) return;
      revisionRef.current = page.revision;
      setState((previous) => ({
        ...previous, sessionKey: owner, filter, items: uniqueItems(items),
        ...snapshotState(page), nextCursor: cursor, loaded: true, loading: false, error: false,
      }));
    } catch {
      if (isCurrent()) setState((previous) => ({ ...previous, loading: false, error: true }));
    } finally {
      if (queryRef.current === controller) queryRef.current = null;
      if (isCurrent()) setState((previous) => ({ ...previous, loading: false }));
    }
  }, []);

  const setFilter = useCallback((filter: NotificationFilter) => {
    if (filterRef.current === filter) return;
    filterRef.current = filter;
    const owner = sessionRef.current;
    setState((previous) => ({ ...previous, filter, items: [], loaded: false, nextCursor: null, error: false }));
    if (owner) void refresh(true);
  }, [refresh]);

  const loadMore = useCallback(async () => {
    const owner = sessionRef.current;
    const previous = stateRef.current;
    if (!owner || previous.sessionKey !== owner || !previous.nextCursor
      || queryRef.current || pageRef.current) return;
    const controller = new AbortController();
    pageRef.current = controller;
    const generation = generationRef.current;
    const filter = filterRef.current;
    const isCurrent = () => mountedRef.current && sessionRef.current === owner
      && generationRef.current === generation && filterRef.current === filter && !controller.signal.aborted;
    setState((current) => ({ ...current, loadingMore: true, pageError: false }));
    try {
      const page = await getNotifications({ filter, cursor: previous.nextCursor, limit: PAGE_SIZE }, controller.signal);
      if (!isCurrent()) return;
      if (page.revision !== previous.revision) {
        void refresh(true);
        return;
      }
      setState((current) => ({
        ...current, items: uniqueItems([...current.items, ...page.items]),
        ...snapshotState(page), nextCursor: page.next_cursor, loadingMore: false,
      }));
    } catch {
      if (isCurrent()) setState((current) => ({ ...current, loadingMore: false, pageError: true }));
    } finally {
      if (pageRef.current === controller) pageRef.current = null;
    }
  }, [refresh]);

  const markAllRead = useCallback(async () => {
    const owner = sessionRef.current;
    const previous = stateRef.current;
    if (!owner || previous.sessionKey !== owner || !previous.loaded || !previous.unreadCount || readRef.current) return;
    const throughRevision = previous.revision;
    const controller = new AbortController();
    readRef.current = controller;
    setState((current) => ({ ...current, readingAll: true, readError: false }));
    try {
      const result = await markAllNotificationsRead(throughRevision, controller.signal);
      if (!mountedRef.current || sessionRef.current !== owner || controller.signal.aborted) return;
      if (result.revision >= revisionRef.current) {
        revisionRef.current = result.revision;
        const readAt = new Date().toISOString();
        setState((current) => ({
          ...current, ...snapshotState(result), readingAll: false,
          items: current.items.map((item) => item.created_revision <= throughRevision && !item.read_at
            ? { ...item, read_at: readAt } : item).filter((item) => current.filter === 'all' || !item.read_at),
        }));
      } else {
        setState((current) => ({ ...current, readingAll: false }));
      }
      void refresh(true);
    } catch {
      if (mountedRef.current && sessionRef.current === owner && !controller.signal.aborted) {
        setState((current) => ({ ...current, readingAll: false, readError: true }));
      }
    } finally {
      if (readRef.current === controller) readRef.current = null;
    }
  }, [refresh]);

  useEffect(() => {
    mountedRef.current = true;
    filterRef.current = 'all';
    revisionRef.current = 0;
    generationRef.current += 1;
    setState(emptyState(sessionKey));
    const refreshVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    refreshVisible();
    const interval = window.setInterval(refreshVisible, POLL_INTERVAL);
    window.addEventListener('focus', refreshVisible);
    window.addEventListener('online', refreshVisible);
    document.addEventListener('visibilitychange', refreshVisible);
    const unsubscribe = subscribeNotificationsChanged((owner) => {
      if ((!owner || owner === sessionKey) && document.visibilityState === 'visible') void refresh(true);
    });
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      queryRef.current?.abort();
      pageRef.current?.abort();
      readRef.current?.abort();
      queryRef.current = null;
      pageRef.current = null;
      readRef.current = null;
      window.clearInterval(interval);
      window.removeEventListener('focus', refreshVisible);
      window.removeEventListener('online', refreshVisible);
      document.removeEventListener('visibilitychange', refreshVisible);
      unsubscribe();
    };
  }, [sessionKey, refresh]);

  // 账号切换的首帧也不暴露旧账号数据，不能等 effect 才清空。
  const currentState = state.sessionKey === sessionKey ? state : emptyState(sessionKey);
  const value = useMemo(() => ({
    ...currentState, setFilter, refresh: () => { void refresh(true); },
    loadMore: () => { void loadMore(); }, markAllRead,
  }), [currentState, setFilter, refresh, loadMore, markAllRead]);
  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}

function snapshotState(snapshot: Pick<NotificationPage, 'unread_count' | 'unread_conversation_ids' | 'revision'>) {
  return { unreadCount: snapshot.unread_count, unreadConversationIds: snapshot.unread_conversation_ids, revision: snapshot.revision };
}

function uniqueItems(items: NotificationItem[]): NotificationItem[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values());
}
