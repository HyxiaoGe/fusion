'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ImageResultItem } from '@/types/conversation';
import ImageViewer from './ImageViewer';
import styles from './AnswerImage.module.css';

/** 源站既不返回图片也不报错时，等这么久后按加载失败显示。 */
export const ANSWER_IMAGE_LOAD_TIMEOUT_MS = 15_000;

/**
 * 回答正文里的配图：地址只来自本条回复的 image_search 结果，并标注来源网站。
 * 点击图片放大查看，来源网站名链接到原网页。
 * 不带 Referer 请求，减少被源站防盗链拦截；加载失败或超时如实显示，不换别的图顶上。
 */
export default function AnswerImage({ image, caption }: { image: ImageResultItem; caption: string }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<'loading' | 'loaded' | 'failed'>('loading');
  const [viewing, setViewing] = useState(false);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const label = caption || image.title;
  const site = image.site_name || image.page_url;

  useEffect(() => {
    setStatus('loading');
    // 已在缓存里的图可能在挂上 onLoad 之前就加载完了。
    const element = imageRef.current;
    if (element?.complete) {
      setStatus(element.naturalWidth > 0 ? 'loaded' : 'failed');
      return undefined;
    }
    const timer = window.setTimeout(
      () => setStatus(current => (current === 'loading' ? 'failed' : current)),
      ANSWER_IMAGE_LOAD_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [image.image_url]);

  return (
    <span className={styles.figure} data-answer-image={image.id}>
      {status === 'failed' ? (
        <span className={styles.failed} role="img" aria-label={label}>
          {t('chatBody.answerImage.loadFailed')}
        </span>
      ) : (
        <button
          type="button"
          className={styles.frame}
          aria-label={t('chatBody.answerImage.view', { caption: label })}
          onClick={() => setViewing(true)}
          disabled={status !== 'loaded'}
        >
          {/* 第三方图片地址，不走 next/image 优化与域名白名单。 */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imageRef}
            className={styles.image}
            src={image.image_url}
            alt={label}
            // 不用 loading="lazy"：未加载的图宽度为 0，浏览器会一直不去请求；且超时从挂载起算，
            // 懒加载会把还没滚到的图误判为失败。每条回答通常只有一到三张图。
            referrerPolicy="no-referrer"
            onLoad={() => setStatus('loaded')}
            onError={() => setStatus('failed')}
          />
        </button>
      )}
      {label || site ? (
        <span className={styles.caption}>
          {label ? <span className={styles.captionText}>{label}</span> : null}
          {site && image.page_url ? (
            <a
              className={styles.site}
              href={image.page_url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={t('chatBody.answerImage.openSource', { site })}
            >
              {site}
            </a>
          ) : site ? (
            <span className={styles.site}>{site}</span>
          ) : null}
        </span>
      ) : null}
      {viewing ? (
        <ImageViewer
          imageUrl={image.image_url}
          alt={label}
          referrerPolicy="no-referrer"
          onClose={() => setViewing(false)}
        />
      ) : null}
    </span>
  );
}
