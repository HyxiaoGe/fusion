'use client';

import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import { Button } from '@/components/ui/button';
import { useHasMounted } from '@/hooks/useHasMounted';

interface ErrorFallbackProps {
  onRetry: () => void;
}

const CONTAINER_CLASS =
  'flex h-full min-h-screen w-full items-center justify-center bg-background px-6 text-foreground';

export default function ErrorFallback({ onRetry }: ErrorFallbackProps) {
  const { t } = useTranslation();
  // 服务端无法得知用户语言，挂载前不渲染文案，避免水合不一致。
  const hasMounted = useHasMounted();

  if (!hasMounted) {
    return <main className={CONTAINER_CLASS} />;
  }

  return (
    <main className={CONTAINER_CLASS}>
      <div className="w-full max-w-md">
        <span className="inline-flex items-center gap-2 rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger opacity-60 motion-reduce:animate-none" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-danger" />
          </span>
          {t('errors.crash.badge')}
        </span>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">{t('errors.crash.title')}</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{t('errors.crash.description')}</p>
        <div className="mt-8 flex gap-3">
          <Button size="lg" type="button" onClick={onRetry}>
            {t('errors.crash.retry')}
          </Button>
          <Button size="lg" variant="outline" asChild>
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- 应用树已出错，用整页跳转丢弃坏状态，而不是客户端路由。 */}
            <a href="/">{t('errors.common.home')}</a>
          </Button>
        </div>
      </div>
    </main>
  );
}
