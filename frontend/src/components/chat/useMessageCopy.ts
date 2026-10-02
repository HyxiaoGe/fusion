'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useToast } from '@/components/ui/toast';

interface UseMessageCopyParams {
  text: string;
}

interface UseMessageCopyResult {
  copied: boolean;
  copy: () => Promise<void>;
}

export function useMessageCopy({ text }: UseMessageCopyParams): UseMessageCopyResult {
  const { toast } = useToast();
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copiedResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const revisionRef = useRef(0);

  useEffect(() => {
    revisionRef.current += 1;
    setCopied(false);
    return () => {
      // 编辑消息或卸载后，旧复制结果不能更新当前消息的反馈。
      revisionRef.current += 1;
      if (copiedResetTimerRef.current) {
        clearTimeout(copiedResetTimerRef.current);
        copiedResetTimerRef.current = null;
      }
    };
  }, [text]);

  const copy = useCallback(async () => {
    if (!text) return;
    const revision = ++revisionRef.current;

    try {
      if (window.isSecureContext && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();

        try {
          const copiedWithFallback = document.execCommand('copy');
          if (!copiedWithFallback) {
            throw new Error('copy command failed');
          }
        } finally {
          document.body.removeChild(textarea);
        }
      }

      if (revision !== revisionRef.current) return;
      setCopied(true);
      if (copiedResetTimerRef.current) {
        clearTimeout(copiedResetTimerRef.current);
      }
      copiedResetTimerRef.current = setTimeout(() => {
        setCopied(false);
        copiedResetTimerRef.current = null;
      }, 2000);
    } catch {
      if (revision !== revisionRef.current) return;
      setCopied(false);
      toast({
        message: t('chatBody.actions.copyFailed'),
        type: 'error',
      });
    }
  }, [text, toast, t]);

  return { copied, copy };
}
