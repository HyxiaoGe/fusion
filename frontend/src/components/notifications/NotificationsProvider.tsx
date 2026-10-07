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
const CACHE_TTL = 15_000;
const FILTERS: NotificationFilter[] = ['all', 'unread'];

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
  ensureFresh: () => void;
  refresh: () => void;
  loadMore: () => void;
  markAllRead: () => Promise<void>;
}

const NotificationsContext = createContext<NotificationsContextValue>({
  ...emptyState(null), setFilter: () => {}, ensureFresh: () => {}, refresh: () => {}, loadMore: () => {}, markAllRead: async () => {},
});

interface CachedList {
  items: NotificationItem[];
  nextCursor: string | null;
  revision: number;
  pagesLoaded: number;
  refreshedAt: number | null;
  loaded: boolean;
  loading: boolean;
  error: boolean;
  loadingMore: boolean;
  pageError: boolean;
}

function emptyCache(): Record<NotificationFilter, CachedList> {
  const list = (): CachedList => ({
    items: [], nextCursor: null, revision: 0, pagesLoaded: 0, refreshedAt: null,
    loaded: false, loading: false, error: false, loadingMore: false, pageError: false,
  });
  return { all: list(), unread: list() };
}

export function useNotifications(): NotificationsContextValue {
  return useContext(NotificationsContext);
}

export function NotificationsProvider({ sessionKey, children }: {
  sessionKey: string | null;
  children: React.ReactNode;
}) {
  const [state, setState] = useState(() => emptyState(sessionKey));
  const sessionRef = useRef(sessionKey);
  sessionRef.current = sessionKey;
  const cacheOwnerRef = useRef(sessionKey);
  const cacheRef = useRef(emptyCache());
  const filterRef = useRef<NotificationFilter>('all');
  // 列表分别缓存；铃铛与侧栏始终使用账号最新快照，不能被旧筛选缓存覆盖。
  const snapshotRef = useRef({ unreadCount: 0, unreadConversationIds: [] as string[], revision: 0 });
  const queryRef = useRef<Record<NotificationFilter, AbortController | null>>({ all: null, unread: null });
  const pageRef = useRef<Record<NotificationFilter, AbortController | null>>({ all: null, unread: null });
  const readRef = useRef<AbortController | null>(null);
  const readStateRef = useRef({ readingAll: false, readError: false });
  const mountedRef = useRef(false);

  const publish = useCallback(() => {
    const owner = sessionRef.current;
    if (!mountedRef.current || cacheOwnerRef.current !== owner) return;
    const filter = filterRef.current;
    const list = cacheRef.current[filter];
    setState({
      sessionKey: owner, filter, items: list.items, nextCursor: list.nextCursor,
      loaded: list.loaded, loading: list.loading, error: list.error,
      loadingMore: list.loadingMore, pageError: list.pageError,
      ...snapshotRef.current, ...readStateRef.current,
    });
  }, []);

  const invalidate = useCallback(() => {
    for (const filter of FILTERS) {
      cacheRef.current[filter].refreshedAt = null;
      cacheRef.current[filter].loading = false;
      cacheRef.current[filter].loadingMore = false;
      queryRef.current[filter]?.abort();
      pageRef.current[filter]?.abort();
      queryRef.current[filter] = null;
      pageRef.current[filter] = null;
    }
  }, []);

  const refreshFilter = useCallback(async (filter: NotificationFilter, force = false) => {
    const owner = sessionRef.current;
    if (!owner || !mountedRef.current || cacheOwnerRef.current !== owner
      || (queryRef.current[filter] && !force)) return;
    queryRef.current[filter]?.abort();
    pageRef.current[filter]?.abort();
    pageRef.current[filter] = null;
    const controller = new AbortController();
    queryRef.current[filter] = controller;
    const list = cacheRef.current[filter];
    const pagesToLoad = Math.max(1, list.pagesLoaded);
    const isCurrent = () => mountedRef.current && sessionRef.current === owner
      && cacheOwnerRef.current === owner && queryRef.current[filter] === controller && !controller.signal.aborted;
    Object.assign(list, { loading: true, error: false, loadingMore: false, pageError: false });
    publish();
    try {
      let page = await getNotifications({ filter, limit: PAGE_SIZE }, controller.signal);
      if (!isCurrent()) return;
      let items = [...page.items];
      let cursor = page.next_cursor;
      let pagesLoaded = 1;
      // 刷新已展开的页，避免轮询不断把用户送回第一页。版本变化时重取首屏，防止拼接旧已读状态。
      for (let index = 1; index < pagesToLoad && cursor; index += 1) {
        const next = await getNotifications({ filter, cursor, limit: PAGE_SIZE }, controller.signal);
        if (!isCurrent()) return;
        if (next.revision !== page.revision) {
          page = await getNotifications({ filter, limit: PAGE_SIZE }, controller.signal);
          if (!isCurrent()) return;
          items = [...page.items];
          cursor = page.next_cursor;
          pagesLoaded = 1;
          break;
        }
        items.push(...next.items);
        cursor = next.next_cursor;
        pagesLoaded += 1;
      }
      if (page.revision < snapshotRef.current.revision) {
        list.refreshedAt = null;
        return;
      }
      if (page.revision > snapshotRef.current.revision) {
        cacheRef.current[filter === 'all' ? 'unread' : 'all'].refreshedAt = null;
      }
      snapshotRef.current = snapshotState(page);
      Object.assign(list, {
        items: uniqueItems(items), nextCursor: cursor, revision: page.revision, pagesLoaded,
        refreshedAt: Date.now(), loaded: true, loading: false, error: false,
      });
    } catch {
      if (isCurrent()) Object.assign(list, { loading: false, error: true, refreshedAt: null });
    } finally {
      if (isCurrent()) {
        queryRef.current[filter] = null;
        list.loading = false;
        publish();
      }
    }
  }, [publish]);

  const ensureFresh = useCallback(() => {
    const filter = filterRef.current;
    const list = cacheRef.current[filter];
    if (list.loaded && list.revision === snapshotRef.current.revision && list.refreshedAt !== null
      && Date.now() - list.refreshedAt < CACHE_TTL) return;
    void refreshFilter(filter);
  }, [refreshFilter]);

  const setFilter = useCallback((filter: NotificationFilter) => {
    if (filterRef.current === filter) return;
    filterRef.current = filter;
    publish();
    ensureFresh();
  }, [publish, ensureFresh]);

  const loadMore = useCallback(async () => {
    const owner = sessionRef.current;
    const filter = filterRef.current;
    const list = cacheRef.current[filter];
    if (!owner || !mountedRef.current || cacheOwnerRef.current !== owner || !list.nextCursor
      || queryRef.current[filter] || pageRef.current[filter]) return;
    if (list.revision !== snapshotRef.current.revision || list.refreshedAt === null) {
      void refreshFilter(filter);
      return;
    }
    const controller = new AbortController();
    pageRef.current[filter] = controller;
    const isCurrent = () => mountedRef.current && sessionRef.current === owner
      && cacheOwnerRef.current === owner && pageRef.current[filter] === controller && !controller.signal.aborted;
    Object.assign(list, { loadingMore: true, pageError: false });
    publish();
    try {
      const page = await getNotifications({ filter, cursor: list.nextCursor, limit: PAGE_SIZE }, controller.signal);
      if (!isCurrent()) return;
      if (page.revision !== list.revision || page.revision < snapshotRef.current.revision) {
        void refreshFilter(filter, true);
        return;
      }
      snapshotRef.current = snapshotState(page);
      Object.assign(list, {
        items: uniqueItems([...list.items, ...page.items]), nextCursor: page.next_cursor,
        pagesLoaded: list.pagesLoaded + 1, loadingMore: false,
      });
    } catch {
      if (isCurrent()) Object.assign(list, { loadingMore: false, pageError: true });
    } finally {
      if (isCurrent()) {
        pageRef.current[filter] = null;
        list.loadingMore = false;
        publish();
      }
    }
  }, [publish, refreshFilter]);

  const markAllRead = useCallback(async () => {
    const owner = sessionRef.current;
    const list = cacheRef.current[filterRef.current];
    if (!owner || !mountedRef.current || cacheOwnerRef.current !== owner || !list.loaded
      || !snapshotRef.current.unreadCount || readRef.current) return;
    const throughRevision = snapshotRef.current.revision;
    const controller = new AbortController();
    readRef.current = controller;
    readStateRef.current = { readingAll: true, readError: false };
    publish();
    try {
      const result = await markAllNotificationsRead(throughRevision, controller.signal);
      if (!mountedRef.current || sessionRef.current !== owner || cacheOwnerRef.current !== owner || controller.signal.aborted) return;
      if (result.revision >= snapshotRef.current.revision) snapshotRef.current = snapshotState(result);
      const readAt = new Date().toISOString();
      for (const filter of FILTERS) {
        cacheRef.current[filter].items = cacheRef.current[filter].items
          .map((item) => item.created_revision <= throughRevision && !item.read_at ? { ...item, read_at: readAt } : item)
          .filter((item) => filter === 'all' || !item.read_at);
      }
      invalidate();
      readStateRef.current.readingAll = false;
      publish();
      void refreshFilter(filterRef.current, true);
    } catch {
      if (mountedRef.current && sessionRef.current === owner && cacheOwnerRef.current === owner && !controller.signal.aborted) {
        readStateRef.current = { readingAll: false, readError: true };
        publish();
      }
    } finally {
      if (readRef.current === controller) readRef.current = null;
    }
  }, [publish, invalidate, refreshFilter]);

  useEffect(() => {
    mountedRef.current = true;
    cacheOwnerRef.current = sessionKey;
    cacheRef.current = emptyCache();
    filterRef.current = 'all';
    snapshotRef.current = { unreadCount: 0, unreadConversationIds: [], revision: 0 };
    readStateRef.current = { readingAll: false, readError: false };
    setState(emptyState(sessionKey));
    const refreshVisible = () => {
      if (document.visibilityState === 'visible') void refreshFilter(filterRef.current);
    };
    refreshVisible();
    const interval = window.setInterval(refreshVisible, POLL_INTERVAL);
    window.addEventListener('focus', refreshVisible);
    window.addEventListener('online', refreshVisible);
    document.addEventListener('visibilitychange', refreshVisible);
    const unsubscribe = subscribeNotificationsChanged((owner) => {
      if (owner && owner !== sessionKey) return;
      // 隐藏期间也失效，避免恢复页面后另一筛选继续保留已读或已删除记录。
      invalidate();
      publish();
      if (document.visibilityState === 'visible') void refreshFilter(filterRef.current, true);
    });
    return () => {
      mountedRef.current = false;
      invalidate();
      readRef.current?.abort();
      readRef.current = null;
      window.clearInterval(interval);
      window.removeEventListener('focus', refreshVisible);
      window.removeEventListener('online', refreshVisible);
      document.removeEventListener('visibilitychange', refreshVisible);
      unsubscribe();
    };
  }, [sessionKey, publish, invalidate, refreshFilter]);

  // 账号切换的首帧也不暴露旧账号数据，不能等 effect 才清空。
  const currentState = state.sessionKey === sessionKey ? state : emptyState(sessionKey);
  const value = useMemo(() => ({
    ...currentState, setFilter, ensureFresh, refresh: () => { void refreshFilter(filterRef.current, true); },
    loadMore: () => { void loadMore(); }, markAllRead,
  }), [currentState, setFilter, ensureFresh, refreshFilter, loadMore, markAllRead]);
  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}

function snapshotState(snapshot: Pick<NotificationPage, 'unread_count' | 'unread_conversation_ids' | 'revision'>) {
  return { unreadCount: snapshot.unread_count, unreadConversationIds: snapshot.unread_conversation_ids, revision: snapshot.revision };
}

function uniqueItems(items: NotificationItem[]): NotificationItem[] {
  return Array.from(new Map(items.map((item) => [item.id, item])).values());
}
