'use client';

import { startTransition, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import ErrorFallback from '@/components/errors/ErrorFallback';

interface AppErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function AppError({ error, reset }: AppErrorProps) {
  const router = useRouter();

  useEffect(() => {
    console.error(error);
  }, [error]);

  const retry = () => {
    // 服务端组件抛出的错误需要先刷新路由数据，单独 reset 只会用旧结果重新渲染。
    startTransition(() => {
      router.refresh();
      reset();
    });
  };

  return <ErrorFallback onRetry={retry} />;
}
