'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, ScrollText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useNotifications } from '@/components/notifications/NotificationsProvider';
import { useHasMounted } from '@/hooks/useHasMounted';
import { getChangelogs, type ChangelogDetail, type ChangelogPage } from '@/lib/api/changelogs';
import { CHANGELOG_ENTRY_PARAM, changelogAnchorId } from '@/lib/routes/changelogRoutes';
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
    <article id={anchor} aria-labelledby={`${anchor}-title`} className="scroll-mt-20 border-b py-12 last:border-b-0 sm:py-14">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span className="rounded-full bg-secondary px-2.5 py-0.5 font-medium text-secondary-foreground">{entry.version}</span>
        <time dateTime={entry.published_at}>{formatDate(entry.published_at)}</time>
      </div>
      <h2 id={`${anchor}-title`} className="mt-4 break-words text-2xl font-semibold tracking-tight">{entry.title}</h2>
      <p className="mt-3 max-w-3xl break-words leading-7 text-muted-foreground">{entry.summary}</p>
      <div ref={bodyRef} className="mt-8 max-w-3xl"><ChangelogContent content={entry.content} /></div>
      {readError && <div role="alert" className="mt-6 flex flex-wrap items-center gap-3 text-sm text-destructive"><p>{t('notifications.readFailed')}</p><Button variant="outline" size="sm" onClick={retryRead}>{t('notifications.retry')}</Button></div>}
    </article>
  );
}

export default function ChangelogList() {
  const { sessionKey } = useNotifications();
  const hasMounted = useHasMounted();
  const router = useRouter();
  const focusId = useSearchParams().get(CHANGELOG_ENTRY_PARAM);
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

  // 从通知进入时定位到对应版本；不在已加载页内就继续翻页，翻完仍没有则停在列表顶部。
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

  const goBack = () => {
    // 从对话里经头像菜单或通知进入时回到原对话；直接打开本页时回到新对话。
    if (window.history.length > 1) router.back();
    else router.push('/');
  };

  // 浏览器语言来自本地存储；首帧使用中性占位，挂载后才显示对应语言。
  if (!hasMounted) return <div aria-hidden="true" className="min-h-screen bg-background"><div className="h-14 border-b" /><div className="mx-auto max-w-6xl px-4 py-12 sm:px-6"><div className="h-9 w-40 rounded bg-muted" /><div className="mt-10 h-48 rounded-xl bg-muted/50" /></div></div>;

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:px-6">
          <Button variant="ghost" size="sm" className="-ml-2 gap-1.5 text-muted-foreground hover:text-foreground" onClick={goBack}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />{t('changelogs.backToChat')}
          </Button>
          <span className="h-4 w-px bg-border" aria-hidden="true" />
          <span className="flex items-center gap-2 text-sm font-medium"><ScrollText className="h-4 w-4 text-primary" aria-hidden="true" />{t('changelogs.title')}</span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-24 sm:px-6" aria-labelledby="changelog-list-title">
        <div className="border-b py-12 sm:py-16">
          <h1 id="changelog-list-title" className="text-3xl font-semibold tracking-tight sm:text-4xl">{t('changelogs.title')}</h1>
          <p className="mt-3 text-muted-foreground">{t('changelogs.description')}</p>
        </div>
        {!sessionKey ? <p className="py-12">{t('changelogs.loginRequired')}</p> : current.error ? (
          <div role="alert" className="flex items-center gap-3 py-12 text-sm"><p>{t('changelogs.loadFailed')}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>{t('notifications.retry')}</Button></div>
        ) : !current.loaded ? <p role="status" className="py-12 text-sm text-muted-foreground">{t('changelogs.loading')}</p> : !current.items.length ? (
          <p className="mt-12 rounded-xl border border-dashed px-5 py-12 text-center text-sm text-muted-foreground">{t('changelogs.empty')}</p>
        ) : (
          <div className="lg:grid lg:grid-cols-[180px_minmax(0,1fr)] lg:gap-16">
            <nav aria-label={t('changelogs.contents')} className="hidden lg:block">
              <ol className="sticky top-24 mt-12 space-y-1 border-l">
                {current.items.map((item) => (
                  <li key={item.id}>
                    <a href={`#${changelogAnchorId(item.id)}`} className="-ml-px block border-l-2 border-transparent py-1.5 pl-4 text-sm text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <span className="block font-medium">{item.version}</span>
                      <span className="block text-xs">{formatDate(item.published_at)}</span>
                    </a>
                  </li>
                ))}
              </ol>
            </nav>
            <div className="min-w-0">
              {current.items.map((item) => <ChangelogEntry key={item.id} sessionKey={sessionKey} entry={item} />)}
              {current.pageError && <p role="alert" className="mt-5 text-sm text-destructive">{t('notifications.loadMoreFailed')}</p>}
              {current.next_cursor && <div className="mt-6 flex justify-center"><Button variant="outline" disabled={current.loadingMore} onClick={() => { void loadMore(); }}>{t(current.loadingMore ? 'changelogs.loading' : current.pageError ? 'notifications.retry' : 'notifications.loadMore')}</Button></div>}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
