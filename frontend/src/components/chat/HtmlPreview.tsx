'use client';

import { RotateCw, X } from 'lucide-react';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
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

/** 沙箱页回报内容高度用的消息类型；父页面只认来自本预览 iframe 的这一种消息 */
export const PREVIEW_HEIGHT_MESSAGE = 'fusion-html-preview-height';

/**
 * 页面内容高度变化时告诉父页面，预览框按内容撑开，不用在固定高度的小框里来回滚。
 * scrollHeight 不会小于当前框高，所以框只会长到刚好放下内容；最高由样式限制在屏幕的八成。
 */
const HEIGHT_REPORTER = '<script>(function(){var last=0;function send(){'
  + 'var h=Math.ceil(document.documentElement.scrollHeight);'
  + `if(h!==last){last=h;parent.postMessage({type:"${PREVIEW_HEIGHT_MESSAGE}",height:h},"*")}}`
  + 'if(window.ResizeObserver){var ro=new ResizeObserver(send);ro.observe(document.documentElement);'
  + 'document.addEventListener("DOMContentLoaded",function(){if(document.body)ro.observe(document.body)})}'
  + 'window.addEventListener("load",send)})();</script>';

const SANDBOX_BOOTSTRAP = STORAGE_SHIM + HEIGHT_REPORTER;

/** 预览框初始高度，也是自动撑开时的下限 */
export const PREVIEW_MIN_HEIGHT = 320;

/**
 * 替身脚本要在页面自己的脚本之前执行，又不能插在 <!DOCTYPE> 前面（会让页面进入怪异模式），
 * 所以依次找 <head>、<html>、DOCTYPE 的结束位置插入，都没有时放在最前面。
 */
export function buildSandboxDocument(html: string): string {
  for (const pattern of [/<head(\s[^>]*)?>/i, /<html(\s[^>]*)?>/i, /<!doctype[^>]*>/i]) {
    const match = pattern.exec(html);
    if (match) {
      const end = match.index + match[0].length;
      return html.slice(0, end) + SANDBOX_BOOTSTRAP + html.slice(end);
    }
  }
  return SANDBOX_BOOTSTRAP + html;
}

/**
 * 下载用的文件名：取页面 <title>（用 DOMParser 解析，只读不执行脚本），去掉文件名里不允许的字符；
 * 没有标题时叫 page.html。下载的是模型给的原始代码，本地打开有真实的 localStorage。
 */
export function htmlDownloadFilename(html: string): string {
  const title = new DOMParser().parseFromString(html, 'text/html').title;
  const safeTitle = title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80);
  return `${safeTitle || 'page'}.html`;
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
  const frameRef = useRef<HTMLIFrameElement>(null);
  // 高度跟着 runId 走：重新运行时回到初始高度，由新页面重新回报。
  const [fit, setFit] = useState({ runId, height: PREVIEW_MIN_HEIGHT });
  const height = fit.runId === runId ? fit.height : PREVIEW_MIN_HEIGHT;

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as { type?: unknown; height?: unknown } | null;
      if (data?.type !== PREVIEW_HEIGHT_MESSAGE || typeof data.height !== 'number' || !Number.isFinite(data.height)) return;
      setFit({ runId, height: Math.max(PREVIEW_MIN_HEIGHT, Math.ceil(data.height)) });
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [runId]);

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
        ref={frameRef}
        className={styles.previewFrame}
        style={{ '--preview-height': `${height}px` } as CSSProperties}
        title={t('chatBody.code.previewTitle')}
        sandbox={HTML_PREVIEW_SANDBOX}
        referrerPolicy="no-referrer"
        srcDoc={buildSandboxDocument(html)}
      />
    </div>
  );
}
