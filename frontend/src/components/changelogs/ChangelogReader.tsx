'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { useNotifications } from '@/components/notifications/NotificationsProvider';
import { useHasMounted } from '@/hooks/useHasMounted';
import { getChangelog, type ChangelogDetail } from '@/lib/api/changelogs';
import { CHANGELOG_PATH } from '@/lib/routes/changelogRoutes';
import { ApiError } from '@/types/api';
import { ChangelogContent } from './ChangelogContent';
import { useReadPresentedChangelog } from './useReadPresentedChangelog';

function PresentedChangelog({ sessionKey, detail }: { sessionKey: string; detail: ChangelogDetail }) {
  const { t, i18n } = useTranslation();
  const bodyRef = useRef<HTMLDivElement>(null);
  const { readError, retryRead } = useReadPresentedChangelog({ sessionKey, changelogId: detail.id, notificationId: detail.notification_id, bodyRef });
  return <article>
    <div className="mt-7 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span className="rounded-full bg-secondary px-3 py-1 font-medium text-secondary-foreground">{detail.version}</span>
      <time dateTime={detail.published_at}>{new Intl.DateTimeFormat(i18n.language, { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(detail.published_at))}</time>
    </div>
    <h1 className="mt-4 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{detail.title}</h1>
    <p className="mt-3 break-words leading-7 text-muted-foreground">{detail.summary}</p>
    <div ref={bodyRef} className="mt-7 border-t pt-7"><ChangelogContent content={detail.content} /></div>
    {readError && <div role="alert" className="mt-6 flex flex-wrap items-center gap-3 text-sm text-destructive"><p>{t('notifications.readFailed')}</p><Button variant="outline" size="sm" onClick={retryRead}>{t('notifications.retry')}</Button></div>}
  </article>;
}

export default function ChangelogReader({ changelogId }: { changelogId: string }) {
  const { sessionKey } = useNotifications();
  const { t } = useTranslation();
  const hasMounted = useHasMounted();
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ owner: string | null; id: string; detail: ChangelogDetail | null; error: 'missing' | 'failed' | null }>(() => ({ owner: null, id: '', detail: null, error: null }));
  const scopeRef = useRef({ sessionKey, changelogId });
  scopeRef.current = { sessionKey, changelogId };

  useEffect(() => {
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && scopeRef.current.sessionKey === sessionKey && scopeRef.current.changelogId === changelogId;
    setState({ owner: sessionKey, id: changelogId, detail: null, error: null });
    if (sessionKey && changelogId) {
      void getChangelog(changelogId, controller.signal).then((detail) => {
        if (isCurrent()) setState({ owner: sessionKey, id: changelogId, detail, error: null });
      }).catch((error: unknown) => {
        if (isCurrent()) setState({ owner: sessionKey, id: changelogId, detail: null, error: error instanceof ApiError && error.code === 'NOT_FOUND' ? 'missing' : 'failed' });
      });
    }
    return () => controller.abort();
  }, [sessionKey, changelogId, retry]);

  const current = state.owner === sessionKey && state.id === changelogId ? state : null;
  // 服务端尚不知晓浏览器语言，避免返回链接与加载文案产生首帧差异。
  if (!hasMounted) return <section aria-hidden="true" className="mx-auto w-full max-w-3xl px-5 py-8 sm:px-8 sm:py-12"><div className="h-7 w-32 rounded bg-muted" /><div className="mt-8 h-32 rounded-xl bg-muted/50" /></section>;
  return <section className="mx-auto w-full max-w-3xl px-5 py-8 sm:px-8 sm:py-12">
    <Link href={CHANGELOG_PATH} className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><ArrowLeft className="h-4 w-4" aria-hidden="true" />{t('changelogs.back')}</Link>
    {!sessionKey ? <p className="mt-8">{t('changelogs.loginRequired')}</p> : current?.error ? (
      <div role="alert" className="mt-8 flex flex-wrap items-center gap-3 text-sm"><p>{t(current.error === 'missing' ? 'changelogs.missing' : 'changelogs.loadFailed')}</p>{current.error === 'failed' && <Button variant="outline" onClick={() => setRetry((value) => value + 1)}>{t('notifications.retry')}</Button>}</div>
    ) : current?.detail ? <PresentedChangelog key={`${sessionKey}:${changelogId}`} sessionKey={sessionKey} detail={current.detail} /> : <p role="status" className="mt-8 text-sm text-muted-foreground">{t('changelogs.loading')}</p>}
  </section>;
}
