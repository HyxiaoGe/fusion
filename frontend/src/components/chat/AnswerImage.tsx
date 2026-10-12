'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ImageResultItem } from '@/types/conversation';
import styles from './AnswerImage.module.css';

/**
 * 回答正文里的配图：地址只来自本条回复的 image_search 结果，并标注来源网站。
 * 不带 Referer 请求，减少被源站防盗链拦截；加载失败时如实显示，不换别的图顶上。
 */
export default function AnswerImage({ image, caption }: { image: ImageResultItem; caption: string }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const label = caption || image.title;
  const site = image.site_name || image.page_url;
  const media = failed ? (
    <span className={styles.failed} role="img" aria-label={label}>
      {t('chatBody.answerImage.loadFailed')}
    </span>
  ) : (
    // 第三方图片地址，不走 next/image 优化与域名白名单。
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={styles.image}
      src={image.image_url}
      alt={label}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
  return (
    <span className={styles.figure} data-answer-image={image.id}>
      {image.page_url ? (
        <a
          className={styles.link}
          href={image.page_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t('chatBody.answerImage.openSource', { site })}
        >
          {media}
        </a>
      ) : media}
      {label || site ? (
        <span className={styles.caption}>
          {label ? <span className={styles.captionText}>{label}</span> : null}
          {site ? <span className={styles.site}>{site}</span> : null}
        </span>
      ) : null}
    </span>
  );
}
