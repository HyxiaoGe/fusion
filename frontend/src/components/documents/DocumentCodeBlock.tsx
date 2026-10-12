'use client';

import { useEffect, useRef, useState } from 'react';
import { Check, ClipboardCopy, Code2, Download, Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import HtmlPreview, { isRunnableHtmlLanguage, useHtmlRunner } from '@/components/chat/HtmlPreview';
import styles from './DocumentCodeBlock.module.css';

type CopyState = 'idle' | 'copying' | 'copied' | 'failed';

export default function DocumentCodeBlock({ language, value, staticView = false, runnable = false }: {
  language?: string;
  value: string;
  staticView?: boolean;
  /** 已保存文档的阅读视图里，HTML 代码块可在沙箱里运行和下载；草稿、对比、导出视图不开放。 */
  runnable?: boolean;
}) {
  const { t } = useTranslation();
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const revision = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    revision.current += 1;
    setCopyState('idle');
    return () => {
      // 流式内容变化或卸载后，旧复制回调不能给新内容显示成功反馈。
      revision.current += 1;
      clearTimeout(timer.current);
    };
  }, [value]);

  const copy = async () => {
    const currentRevision = revision.current;
    clearTimeout(timer.current);
    setCopyState('copying');
    try {
      await navigator.clipboard.writeText(value);
      if (revision.current !== currentRevision) return;
      setCopyState('copied');
      timer.current = setTimeout(() => setCopyState('idle'), 2000);
    } catch {
      if (revision.current === currentRevision) setCopyState('failed');
    }
  };

  const html = useHtmlRunner(value);
  const canRun = runnable && !staticView && isRunnableHtmlLanguage(language);
  const label = language || t('documents.code.plainText');
  return (
    <div className={cn(styles.block, 'fdoc-code-block')}>
      <div className={cn(styles.header, 'fdoc-code-header')}>
        <span className={cn(styles.language, 'fdoc-code-language')}><Code2 aria-hidden="true" />{label}</span>
        {!staticView ? (
          <div className={styles.actions}>
            {canRun ? (
              <>
                <button
                  type="button"
                  className={styles.copyButton}
                  aria-label={t('chatBody.code.runLabel')}
                  onClick={html.run}
                  onPointerMove={pointGlassLight}
                  onPointerLeave={resetGlassLight}
                >
                  <GlassHoverLens corners={false} />
                  <Play aria-hidden="true" />
                  <span>{t(html.preview ? 'chatBody.code.rerun' : 'chatBody.code.run')}</span>
                </button>
                <button
                  type="button"
                  className={styles.copyButton}
                  aria-label={t('chatBody.code.downloadLabel')}
                  onClick={html.download}
                  onPointerMove={pointGlassLight}
                  onPointerLeave={resetGlassLight}
                >
                  <GlassHoverLens corners={false} />
                  <Download aria-hidden="true" />
                  <span>{t('chatBody.code.download')}</span>
                </button>
              </>
            ) : null}
            <button
              type="button"
              className={styles.copyButton}
              aria-label={t('documents.code.copyLabel')}
              disabled={copyState === 'copying'}
              onClick={() => { void copy(); }}
              onPointerMove={pointGlassLight}
              onPointerLeave={resetGlassLight}
            >
              <GlassHoverLens corners={false} />
              {copyState === 'copied' ? <Check aria-hidden="true" /> : <ClipboardCopy aria-hidden="true" />}
              <span aria-live="polite">{t(`documents.code.${copyState === 'copied' ? 'copied' : copyState === 'copying' ? 'copying' : 'copy'}`)}</span>
            </button>
          </div>
        ) : null}
      </div>
      {copyState === 'failed' ? <p role="alert" className={styles.error}>{t('documents.code.failed')}</p> : null}
      <pre className={cn(styles.code, 'fdoc-code-content')} tabIndex={staticView ? undefined : 0}>
        <code className={language ? `language-${language}` : undefined}>{value}</code>
      </pre>
      {canRun && html.preview ? (
        <HtmlPreview html={html.preview.html} runId={html.preview.runId} onRerun={html.run} onClose={html.close} />
      ) : null}
    </div>
  );
}
