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

/**
 * 沙箱页没有同源权限，访问 localStorage / sessionStorage 会直接抛错，
 * 常见的待办、计分类示例就会在那一行中断。这里先注入只存在内存里的替身：
 * 页面能正常读写，关闭或重新运行即清空，读不到也写不进主站的真实存储。
 */
const STORAGE_SHIM = '<script>(function(){function m(){var d=new Map();return{'
  + 'getItem:function(k){k=String(k);return d.has(k)?d.get(k):null},'
  + 'setItem:function(k,v){d.set(String(k),String(v))},'
  + 'removeItem:function(k){d.delete(String(k))},'
  + 'clear:function(){d.clear()},'
  + 'key:function(i){var a=Array.from(d.keys());return i<a.length?a[i]:null},'
  + 'get length(){return d.size}}}'
  + '["localStorage","sessionStorage"].forEach(function(n){try{window[n]}catch(e){'
  + 'try{Object.defineProperty(window,n,{value:m(),configurable:true})}catch(_){}}})})();</script>';

/**
 * 替身脚本要在页面自己的脚本之前执行，又不能插在 <!DOCTYPE> 前面（会让页面进入怪异模式），
 * 所以依次找 <head>、<html>、DOCTYPE 的结束位置插入，都没有时放在最前面。
 */
export function buildSandboxDocument(html: string): string {
  for (const pattern of [/<head(\s[^>]*)?>/i, /<html(\s[^>]*)?>/i, /<!doctype[^>]*>/i]) {
    const match = pattern.exec(html);
    if (match) {
      const end = match.index + match[0].length;
      return html.slice(0, end) + STORAGE_SHIM + html.slice(end);
    }
  }
  return STORAGE_SHIM + html;
}

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
        srcDoc={buildSandboxDocument(html)}
      />
    </div>
  );
}
