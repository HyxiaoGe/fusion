import { useEffect, useState } from 'react';
import { registerBackgroundTask } from '@/lib/scheduler/backgroundScheduler';

const CHECK_INTERVAL_MS = 5 * 60 * 1000;
const CURRENT_VERSION = process.env.NEXT_PUBLIC_BUILD_VERSION ?? '';

/** 旧页面懒加载已被新部署替换掉的代码文件时会失败，这同样说明线上已有新版本。 */
function isChunkLoadFailure(reason: unknown): boolean {
  const error = reason as { name?: unknown; message?: unknown } | null;
  if (!error) return false;
  if (error.name === 'ChunkLoadError') return true;
  return typeof error.message === 'string' && error.message.includes('Loading chunk');
}

/** 返回线上新版本号；没有新版本时为 null。 */
export function useNewVersionAvailable(): string | null {
  const [latestVersion, setLatestVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!CURRENT_VERSION) return undefined;
    let inFlight = false;
    const check = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const response = await fetch('/build-version', { cache: 'no-store' });
        if (!response.ok) return;
        const { version } = (await response.json()) as { version?: unknown };
        if (typeof version === 'string' && version && version !== CURRENT_VERSION) setLatestVersion(version);
      } catch {
        // 网络抖动或部署切换中，下个周期再查。
      } finally {
        inFlight = false;
      }
    };
    const unregister = registerBackgroundTask(() => void check(), {
      intervalMs: CHECK_INTERVAL_MS,
      runOnStart: false,
      minGapMs: 60_000,
    });
    const handleChunkError = (event: ErrorEvent | PromiseRejectionEvent) => {
      const reason = 'reason' in event ? event.reason : event.error;
      if (isChunkLoadFailure(reason)) void check();
    };
    window.addEventListener('error', handleChunkError);
    window.addEventListener('unhandledrejection', handleChunkError);
    return () => {
      unregister();
      window.removeEventListener('error', handleChunkError);
      window.removeEventListener('unhandledrejection', handleChunkError);
    };
  }, []);

  return latestVersion;
}
