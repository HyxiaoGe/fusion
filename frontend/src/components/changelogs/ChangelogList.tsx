'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, ScrollText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useNotifications } from '@/components/notifications/NotificationsProvider';
import { useHasMounted } from '@/hooks/useHasMounted';
import { getChangelogs, type ChangelogPage } from '@/lib/api/changelogs';
import { buildChangelogPath } from '@/lib/routes/changelogRoutes';

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

export default function ChangelogList() {
  const { sessionKey } = useNotifications();
  const hasMounted = useHasMounted();
  const { t, i18n } = useTranslation();
  const [state, setState] = useState(() => emptyList(null));
  const [retry, setRetry] = useState(0);
  const ownerRef = useRef(sessionKey);
  ownerRef.current = sessionKey;
  const pageRequestRef = useRef<AbortController | null>(null);

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

  // 浏览器语言来自本地存储；首帧使用中性占位，挂载后才显示对应语言。
  if (!hasMounted) return <section aria-hidden="true" className="mx-auto w-full max-w-3xl px-5 py-8 sm:px-8 sm:py-12"><div className="h-7 w-32 rounded bg-muted" /><div className="mt-8 h-32 rounded-xl bg-muted/50" /></section>;

  return (
    <section className="mx-auto w-full max-w-3xl px-5 py-8 sm:px-8 sm:py-12" aria-labelledby="changelog-list-title">
      <div className="mb-8 flex items-center gap-3">
        <ScrollText className="h-6 w-6 text-primary" aria-hidden="true" />
        <div><h1 id="changelog-list-title" className="text-2xl font-semibold tracking-tight">{t('changelogs.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('changelogs.description')}</p></div>
      </div>
      {!sessionKey ? <p>{t('changelogs.loginRequired')}</p> : current.error ? (
        <div role="alert" className="flex items-center gap-3 text-sm"><p>{t('changelogs.loadFailed')}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>{t('notifications.retry')}</Button></div>
      ) : !current.loaded ? <p role="status" className="text-sm text-muted-foreground">{t('changelogs.loading')}</p> : !current.items.length ? (
        <p className="rounded-xl border border-dashed px-5 py-12 text-center text-sm text-muted-foreground">{t('changelogs.empty')}</p>
      ) : (
        <ul className="space-y-4">{current.items.map((item) => (
          <li key={item.id}><Link href={buildChangelogPath(item.id)} className="group block rounded-xl border bg-card p-5 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span className="rounded-full bg-secondary px-2.5 py-1 font-medium text-secondary-foreground">{item.version}</span>
              <time dateTime={item.published_at}>{new Intl.DateTimeFormat(i18n.language, { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(item.published_at))}</time>
            </div>
            <div className="flex items-start justify-between gap-3"><h2 className="break-words text-lg font-semibold">{item.title}</h2><ArrowRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground group-hover:text-foreground" aria-hidden="true" /></div>
            <p className="mt-2 break-words text-sm leading-6 text-muted-foreground">{item.summary}</p>
          </Link></li>
        ))}</ul>
      )}
      {current.pageError && <p role="alert" className="mt-5 text-sm text-destructive">{t('notifications.loadMoreFailed')}</p>}
      {sessionKey && current.next_cursor && <div className="mt-6 flex justify-center"><Button variant="outline" disabled={current.loadingMore} onClick={() => { void loadMore(); }}>{t(current.loadingMore ? 'changelogs.loading' : current.pageError ? 'notifications.retry' : 'notifications.loadMore')}</Button></div>}
    </section>
  );
}
