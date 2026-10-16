'use client';

import { useTranslation } from 'react-i18next';
import type { VideoResultItem } from '@/types/conversation';
import styles from './AnswerVideo.module.css';

/** 各平台官方外链播放器地址；视频 id 已在解码时按平台格式校验，地址只由这里拼出。 */
export function answerVideoEmbedUrl(video: VideoResultItem): string {
  const id = encodeURIComponent(video.video_id);
  switch (video.platform) {
    case 'bilibili':
      return `https://player.bilibili.com/player.html?bvid=${id}&autoplay=0&danmaku=0`;
    case 'douyin':
      return `https://open.douyin.com/player/video?vid=${id}&autoplay=0`;
    case 'tencent':
      return `https://v.qq.com/txp/iframe/player.html?vid=${id}`;
  }
}

/**
 * 回答正文里的视频：只嵌入本条回复 video_search 结果里的视频，用平台官方播放器，并链接到原视频页。
 * 元素都是 span 与 iframe，可以合法地放在 <p> 里；样式上单独占一行。
 */
export default function AnswerVideo({ video, caption }: { video: VideoResultItem; caption: string }) {
  const { t } = useTranslation();
  const label = caption || video.title;
  const platform = t(`chatBody.answerVideo.${video.platform}`);

  return (
    <span
      className={video.platform === 'douyin' ? `${styles.figure} ${styles.vertical}` : styles.figure}
      data-answer-video={video.id}
    >
      <span className={styles.frame}>
        <iframe
          className={styles.player}
          src={answerVideoEmbedUrl(video)}
          title={label || platform}
          loading="lazy"
          allow="fullscreen; encrypted-media; picture-in-picture"
          allowFullScreen
        />
      </span>
      <span className={styles.caption}>
        {label ? <span className={styles.captionText}>{label}</span> : null}
        <a
          className={styles.site}
          href={video.page_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t('chatBody.answerVideo.openSource', { platform, caption: label })}
        >
          {platform}
        </a>
      </span>
    </span>
  );
}
