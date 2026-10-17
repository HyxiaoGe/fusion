'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import { FusionMark } from '@/components/brand/FusionMark';
import { Button } from '@/components/ui/button';
import { useHasMounted } from '@/hooks/useHasMounted';
import { ParticleText } from './ParticleText';

const TYPE_START_DELAY_MS = 400;
const TYPE_INTERVAL_MS = 32;

function decodePath(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

export default function NotFoundView() {
  const { t } = useTranslation();
  const router = useRouter();
  const path = decodePath(usePathname() ?? '/');
  const lead = t('errors.notFound.lead');
  const tail = t('errors.notFound.tail');
  const total = lead.length + path.length + tail.length;
  const [typed, setTyped] = useState(0);
  // 服务端无法得知用户语言，挂载前不渲染文案，避免水合不一致。
  const hasMounted = useHasMounted();

  useEffect(() => {
    if (prefersReducedMotion()) {
      setTyped(total);
      return;
    }
    setTyped(0);
    let count = 0;
    let interval: ReturnType<typeof setInterval> | undefined;
    const start = setTimeout(() => {
      interval = setInterval(() => {
        count += 1;
        setTyped(count);
        if (count >= total) clearInterval(interval);
      }, TYPE_INTERVAL_MS);
    }, TYPE_START_DELAY_MS);
    return () => {
      clearTimeout(start);
      clearInterval(interval);
    };
  }, [total]);

  if (!hasMounted) {
    return <main className="h-full w-full bg-background" />;
  }

  const shownLead = lead.slice(0, typed);
  const shownPath = path.slice(0, Math.max(0, typed - lead.length));
  const shownTail = tail.slice(0, Math.max(0, typed - lead.length - path.length));
  const done = typed >= total;

  const goBack = () => {
    if (window.history.length > 1) {
      router.back();
    } else {
      router.push('/');
    }
  };

  return (
    <main className="flex h-full w-full flex-col items-center justify-center bg-background px-6 text-foreground">
      <h1 className="sr-only">{t('errors.notFound.title')}</h1>
      <div className="relative h-[min(300px,40vw)] w-[min(720px,92vw)]">
        <ParticleText text="404" label={t('errors.notFound.title')} />
        <p className="pointer-events-none absolute -bottom-1.5 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs text-muted-foreground/70">
          {t('errors.notFound.hint')}
        </p>
      </div>

      <div className="mt-10 flex w-full max-w-[520px] items-start gap-3">
        <div
          aria-hidden="true"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-border bg-card text-foreground"
        >
          <FusionMark className="h-5 w-5" />
        </div>
        <p className="min-h-[54px] flex-1 rounded-2xl rounded-tl-sm border border-border bg-card px-[18px] py-3.5 text-[15px] leading-7 text-card-foreground">
          <span className="sr-only">
            {lead} {path} {tail}
          </span>
          <span aria-hidden="true">
            {shownLead}
            {shownPath && (
              <code className="mx-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[13px] [overflow-wrap:anywhere]">
                {shownPath}
              </code>
            )}
            {shownTail}
            {!done && (
              <span className="ml-0.5 inline-block h-[1.05em] w-[7px] animate-pulse bg-foreground align-text-bottom" />
            )}
          </span>
        </p>
      </div>

      <div className="mt-7 flex gap-3">
        <Button size="lg" asChild>
          <Link href="/">{t('errors.common.home')}</Link>
        </Button>
        <Button size="lg" variant="outline" type="button" onClick={goBack}>
          {t('errors.notFound.back')}
        </Button>
      </div>
    </main>
  );
}
