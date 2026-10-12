'use client';

import { RotateCw, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import styles from './CodeBlock.module.css';

/**
 * 在沙箱 iframe 里运行一段 HTML。
 *
 * 只给 allow-scripts（和弹窗），不给 allow-same-origin：页面被当作独立的不透明来源，
 * 读不到主站的 cookie、localStorage 和登录态，也不能操作父页面；
 * 不给 allow-top-navigation / allow-popups，代码无法把主站跳走或开新窗口逃出沙箱。
 */
export const HTML_PREVIEW_SANDBOX = 'allow-scripts allow-modals';

interface HtmlPreviewProps {
  /** 点击运行时的代码快照；流式输出后续变化不会自动刷新，需要重新运行 */
  html: string;
  /** 每次运行递增，用来强制重建 iframe，让脚本从头执行 */
  runId: number;
  onRerun: () => void;
  onClose: () => void;
}

export default function HtmlPreview({ html, runId, onRerun, onClose }: HtmlPreviewProps) {
  const { t } = useTranslation();

  return (
    <div className={styles.preview} data-testid="html-preview">
      <div className={styles.previewBar}>
        <span>{t('chatBody.code.previewLabel')}</span>
        <div className={styles.previewActions}>
          <button type="button" className={styles.previewButton} onClick={onRerun} aria-label={t('chatBody.code.rerun')}>
            <RotateCw aria-hidden="true" />
          </button>
          <button type="button" className={styles.previewButton} onClick={onClose} aria-label={t('chatBody.code.closePreview')}>
            <X aria-hidden="true" />
          </button>
        </div>
      </div>
      <iframe
        key={runId}
        className={styles.previewFrame}
        title={t('chatBody.code.previewTitle')}
        sandbox={HTML_PREVIEW_SANDBOX}
        referrerPolicy="no-referrer"
        srcDoc={html}
      />
    </div>
  );
}
