'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { getChangelogs, type ChangelogDetail, type ChangelogPage } from '@/lib/api/changelogs';
import { ChangelogContent } from './ChangelogContent';
import { useReadPresentedChangelog } from './useReadPresentedChangelog';

interface ListState extends ChangelogPage {
  owner: string | null;
  loaded: boolean;
  error: boolean;
  loadingMore: boolean;
  pageError: boolean;
}

function emptyList(owner: string | null): ListState {
  return { owner, items: [], next_cursor: null, loaded: false, error: false, loadingMore: false, pageError: false };
}

export function changelogAnchorId(id: string): string {
  return `changelog-${id}`;
}

function useFormatDate() {
  const { i18n } = useTranslation();
  return (value: string) => new Intl.DateTimeFormat(i18n.language, { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(value));
}

function ChangelogEntry({ sessionKey, entry }: { sessionKey: string; entry: ChangelogDetail }) {
  const { t } = useTranslation();
  const formatDate = useFormatDate();
  const bodyRef = useRef<HTMLDivElement>(null);
  const { readError, retryRead } = useReadPresentedChangelog({ sessionKey, changelogId: entry.id, notificationId: entry.notification_id, bodyRef });
  const anchor = changelogAnchorId(entry.id);
  return (
    <article id={anchor} aria-labelledby={`${anchor}-title`} className="scroll-mt-4 border-b py-8 last:border-b-0">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span className="rounded-full bg-secondary px-2.5 py-0.5 font-medium text-secondary-foreground">{entry.version}</span>
        <time dateTime={entry.published_at}>{formatDate(entry.published_at)}</time>
      </div>
      <h3 id={`${anchor}-title`} className="mt-3 break-words text-xl font-semibold tracking-tight">{entry.title}</h3>
      <p className="mt-2 break-words leading-7 text-muted-foreground">{entry.summary}</p>
      <div ref={bodyRef} className="mt-6"><ChangelogContent content={entry.content} /></div>
      {readError && <div role="alert" className="mt-6 flex flex-wrap items-center gap-3 text-sm text-destructive"><p>{t('notifications.readFailed')}</p><Button variant="outline" size="sm" onClick={retryRead}>{t('notifications.retry')}</Button></div>}
    </article>
  );
}

/** 更新日志按时间倒序完整展开：左侧版本目录，右侧正文在自身滚动区内滚动。 */
export default function ChangelogTimeline({ sessionKey, focusId }: { sessionKey: string | null; focusId: string | null }) {
  const { t } = useTranslation();
  const formatDate = useFormatDate();
  const [state, setState] = useState(() => emptyList(null));
  const [retry, setRetry] = useState(0);
  const ownerRef = useRef(sessionKey);
  ownerRef.current = sessionKey;
  const pageRequestRef = useRef<AbortController | null>(null);
  const focusedRef = useRef('');

  useEffect(() => {
    const controller = new AbortController();
    pageRequestRef.current?.abort();
    pageRequestRef.current = null;
    setState(emptyList(sessionKey));
    if (sessionKey) {
      void getChangelogs({}, controller.signal).then((page) => {
        if (!controller.signal.aborted && ownerRef.current === sessionKey) {
          setState({ ...emptyList(sessionKey), ...page, loaded: true });
        }
      }).catch(() => {
        if (!controller.signal.aborted && ownerRef.current === sessionKey) setState({ ...emptyList(sessionKey), error: true });
      });
    }
    return () => { controller.abort(); pageRequestRef.current?.abort(); pageRequestRef.current = null; };
  }, [sessionKey, retry]);

  const current = state.owner === sessionKey ? state : emptyList(sessionKey);
  const loadMore = async () => {
    if (!sessionKey || !current.next_cursor || pageRequestRef.current) return;
    const controller = new AbortController();
    const cursor = current.next_cursor;
    pageRequestRef.current = controller;
    setState((previous) => ({ ...previous, loadingMore: true, pageError: false }));
    try {
      const page = await getChangelogs({ cursor }, controller.signal);
      if (controller.signal.aborted || ownerRef.current !== sessionKey || pageRequestRef.current !== controller) return;
      setState((previous) => {
        const ids = new Set(previous.items.map((item) => item.id));
        return { ...previous, items: [...previous.items, ...page.items.filter((item) => !ids.has(item.id))], next_cursor: page.next_cursor, loadingMore: false };
      });
    } catch {
      if (!controller.signal.aborted && ownerRef.current === sessionKey && pageRequestRef.current === controller) {
        setState((previous) => ({ ...previous, loadingMore: false, pageError: true }));
      }
    } finally {
      if (pageRequestRef.current === controller) pageRequestRef.current = null;
    }
  };
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

  // 从通知进入时定位到对应版本；不在已加载页内就继续翻页，翻完仍没有则停在最新一篇。
  const focusKey = sessionKey && focusId ? `${sessionKey}:${focusId}` : '';
  const focusLoaded = current.items.some((item) => item.id === focusId);
  const canLoadMore = current.loaded && Boolean(current.next_cursor) && !current.loadingMore && !current.pageError;
  useEffect(() => {
    if (!focusKey || !focusId || focusedRef.current === focusKey) return;
    if (focusLoaded) {
      focusedRef.current = focusKey;
      document.getElementById(changelogAnchorId(focusId))?.scrollIntoView({ block: 'start' });
    } else if (canLoadMore) {
      void loadMoreRef.current();
    }
  }, [focusKey, focusId, focusLoaded, canLoadMore]);

  const status = !sessionKey ? <p className="p-6">{t('changelogs.loginRequired')}</p> : current.error ? (
    <div role="alert" className="flex items-center gap-3 p-6 text-sm"><p>{t('changelogs.loadFailed')}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>{t('notifications.retry')}</Button></div>
  ) : !current.loaded ? <p role="status" className="p-6 text-sm text-muted-foreground">{t('changelogs.loading')}</p> : !current.items.length ? (
    <p className="m-6 rounded-xl border border-dashed px-5 py-12 text-center text-sm text-muted-foreground">{t('changelogs.empty')}</p>
  ) : null;
  if (status || !sessionKey) return <div className="min-h-0 flex-1 overflow-y-auto">{status}</div>;

  return (
    <div className="flex min-h-0 flex-1">
      <nav aria-label={t('changelogs.contents')} className="hidden w-44 shrink-0 overflow-y-auto border-r py-4 md:block">
        <ol className="space-y-0.5 px-3">
          {current.items.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => document.getElementById(changelogAnchorId(item.id))?.scrollIntoView({ block: 'start', behavior: 'smooth' })}
                className="block w-full rounded-md px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="block font-medium">{item.version}</span>
                <span className="block text-xs">{formatDate(item.published_at)}</span>
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div data-testid="changelog-scroll" className="min-w-0 flex-1 overflow-y-auto px-6 pb-8">
        {current.items.map((item) => <ChangelogEntry key={item.id} sessionKey={sessionKey} entry={item} />)}
        {current.pageError && <p role="alert" className="mt-5 text-sm text-destructive">{t('notifications.loadMoreFailed')}</p>}
        {current.next_cursor && <div className="mt-6 flex justify-center"><Button variant="outline" disabled={current.loadingMore} onClick={() => { void loadMore(); }}>{t(current.loadingMore ? 'changelogs.loading' : current.pageError ? 'notifications.retry' : 'notifications.loadMore')}</Button></div>}
      </div>
    </div>
  );
}
