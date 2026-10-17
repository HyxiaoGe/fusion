'use client';

import './globals.css';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import ErrorFallback from '@/components/errors/ErrorFallback';
import { useResolvedTheme } from '@/lib/hooks/useResolvedTheme';
import { getSavedTheme } from '@/redux/slices/themeSlice';

interface GlobalErrorProps {
  error: Error & { digest?: string };
}

export default function GlobalError({ error }: GlobalErrorProps) {
  const { i18n } = useTranslation();
  // 根布局已崩溃，Redux 和 ThemeClassSync 都不可用，直接读取用户保存的主题偏好。
  const [themeMode, setThemeMode] = useState<'light' | 'dark' | 'system'>('system');
  const theme = useResolvedTheme(themeMode);

  useEffect(() => {
    setThemeMode(getSavedTheme());
  }, []);

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang={i18n.language || 'zh-CN'} className={theme} style={{ colorScheme: theme }}>
      <body className="min-h-screen bg-background font-sans text-sm antialiased">
        <ErrorFallback onRetry={() => window.location.reload()} />
      </body>
    </html>
  );
}
