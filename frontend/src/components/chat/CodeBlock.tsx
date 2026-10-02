'use client';

import { Button } from '@/components/ui/button';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import { cn } from '@/lib/utils';
import { Check, ClipboardCopy, FileText, Hash, ChevronDown, ChevronUp } from 'lucide-react';
import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import hljs from 'highlight.js';
import styles from './CodeBlock.module.css';

interface CodeBlockProps {
  language: string;
  value: string;
  showLineNumbers?: boolean;
  className?: string;
  maxLines?: number; // 最大显示行数，超过则可折叠
}

type CopyState = 'idle' | 'copying' | 'copied' | 'failed';

const LANGUAGE_MAP: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  rb: 'ruby',
  sh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  html: 'xml',
  vue: 'xml',
  svelte: 'xml',
};

const DISPLAY_LANGUAGE_MAP: Record<string, string> = {
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  python: 'Python',
  java: 'Java',
  cpp: 'C++',
  csharp: 'C#',
  php: 'PHP',
  ruby: 'Ruby',
  go: 'Go',
  rust: 'Rust',
  swift: 'Swift',
  kotlin: 'Kotlin',
  html: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  less: 'LESS',
  xml: 'XML',
  json: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  toml: 'TOML',
  ini: 'INI',
  bash: 'Bash',
  shell: 'Shell',
  powershell: 'PowerShell',
  sql: 'SQL',
  markdown: 'Markdown',
};

function getDisplayLanguage(language: string): string {
  return DISPLAY_LANGUAGE_MAP[language.toLowerCase()] || language.toUpperCase();
}

function highlightCode(value: string, language: string): string {
  const actualLanguage = LANGUAGE_MAP[language.toLowerCase()] || language.toLowerCase();

  try {
    if (hljs.getLanguage(actualLanguage)) {
      return hljs.highlight(value, { language: actualLanguage }).value;
    }
    return hljs.highlightAuto(value).value;
  } catch {
    return hljs.highlight(value, { language: 'plaintext' }).value;
  }
}

const CodeBlock: React.FC<CodeBlockProps> = ({ 
  language, 
  value, 
  showLineNumbers = true,
  className,
  maxLines = 15 // 默认最大显示15行
}) => {
  const { t } = useTranslation();
  const [copyState, setCopyState] = useState<CopyState>('idle');
  const [isExpanded, setIsExpanded] = useState(false);
  const revision = useRef(0);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const contentId = useId();

  useEffect(() => {
    revision.current += 1;
    setCopyState('idle');
    return () => {
      // 内容更新或卸载后，旧复制回调不能覆盖当前代码的反馈。
      revision.current += 1;
      clearTimeout(copyTimer.current);
    };
  }, [value]);

  const handleCopy = async () => {
    const currentRevision = revision.current;
    clearTimeout(copyTimer.current);
    setCopyState('copying');
    try {
      await navigator.clipboard.writeText(value);
      if (revision.current !== currentRevision) return;
      setCopyState('copied');
      copyTimer.current = setTimeout(() => setCopyState('idle'), 2000);
    } catch {
      if (revision.current === currentRevision) setCopyState('failed');
    }
  };

  // 生成行号
  const generateLineNumbers = (code: string): string[] => {
    return code.split('\n').map((_, index) => (index + 1).toString());
  };

  const lineNumbers = useMemo(() => generateLineNumbers(value), [value]);
  const maxLineNumberWidth = lineNumbers.length.toString().length;
  const totalLines = lineNumbers.length;
  const shouldShowCollapse = totalLines > maxLines;
  const isCollapsed = shouldShowCollapse && !isExpanded;
  const displayValue = useMemo(
    () => isCollapsed ? value.split('\n').slice(0, maxLines).join('\n') : value,
    [isCollapsed, maxLines, value],
  );
  const displayCode = useMemo(
    () => highlightCode(displayValue, language),
    [displayValue, language],
  );
  const displayLineNumbers = isCollapsed ? lineNumbers.slice(0, maxLines) : lineNumbers;
  const displayLanguage = !language || ['text', 'plaintext'].includes(language.toLowerCase())
    ? t('chatBody.code.plainText')
    : getDisplayLanguage(language);
  const copyLabel = t(`chatBody.code.${copyState === 'copied' ? 'copied' : copyState === 'copying' ? 'copying' : 'copy'}`);

  return (
    <div className={cn(styles.block, className)} data-code-block="true">
      {/* 代码块头部 */}
      <div className={styles.header}>
        <div className={styles.metadata}>
          <FileText aria-hidden="true" />
          <span className={styles.language}>
            {displayLanguage}
          </span>
          {showLineNumbers && (
            <div className={styles.lineCount}>
              <Hash aria-hidden="true" />
              <span>
                {isCollapsed && shouldShowCollapse 
                  ? t('chatBody.code.collapsedLineCount', { visible: maxLines, total: totalLines })
                  : t('chatBody.code.lineCount', { count: totalLines })
                }
              </span>
            </div>
          )}
          {shouldShowCollapse && (
            <Button
              variant="ghost"
              size="sm"
              className={styles.iconButton}
              onClick={() => setIsExpanded(isCollapsed)}
              onPointerMove={pointGlassLight}
              onPointerLeave={resetGlassLight}
              title={t(`chatBody.code.${isCollapsed ? 'expand' : 'collapse'}`)}
              aria-label={t(`chatBody.code.${isCollapsed ? 'expand' : 'collapse'}`)}
              aria-expanded={!isCollapsed}
              aria-controls={contentId}
            >
              <GlassHoverLens corners={false} />
              {isCollapsed ? (
                <ChevronDown aria-hidden="true" />
              ) : (
                <ChevronUp aria-hidden="true" />
              )}
            </Button>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className={cn(styles.copyButton, copyState === 'copied' && styles.success)}
          onClick={() => { void handleCopy(); }}
          onPointerMove={pointGlassLight}
          onPointerLeave={resetGlassLight}
          title={t('chatBody.code.copyLabel')}
          aria-label={t('chatBody.code.copyLabel')}
          disabled={copyState === 'copying'}
        >
          <GlassHoverLens corners={false} />
          {copyState === 'copied' ? (
            <Check aria-hidden="true" />
          ) : (
            <ClipboardCopy aria-hidden="true" />
          )}
          <span aria-live="polite">{copyLabel}</span>
        </Button>
      </div>

      {copyState === 'failed' ? (
        <p role="alert" className={styles.error}>{t('chatBody.code.failed')}</p>
      ) : null}

      {/* 代码内容 */}
      <div
        id={contentId}
        className={styles.scroll}
        role="region"
        aria-label={t('chatBody.code.contentLabel', { language: displayLanguage })}
        tabIndex={0}
      >
          <div className={styles.row}>
            {/* 行号列 */}
            {showLineNumbers && (
              <div 
                className={styles.gutter}
                aria-hidden="true"
                style={{ width: `${Math.max(maxLineNumberWidth * 0.6 + 1, 2.5)}rem` }}
              >
                {displayLineNumbers.map((lineNum, index) => (
                  <div 
                    key={index} 
                    className={styles.lineNumber}
                  >
                    {lineNum}
                  </div>
                ))}
              </div>
            )}

            {/* 代码列 */}
            <div className={styles.codeColumn}>
              <pre className={styles.code}>
                <code 
                  className={cn(
                    `language-${language}`
                  )}
                  dangerouslySetInnerHTML={{ __html: displayCode }}
                />
              </pre>
              
              {/* 折叠时显示的省略提示 */}
              {isCollapsed && shouldShowCollapse && (
                <div className={styles.expand}>
                  <div>
                    <Button
                      variant="outline"
                      size="sm"
                      className={styles.expandButton}
                      onClick={() => setIsExpanded(true)}
                      onPointerMove={pointGlassLight}
                      onPointerLeave={resetGlassLight}
                      aria-expanded={false}
                      aria-controls={contentId}
                    >
                      <GlassHoverLens corners={false} />
                      <ChevronDown aria-hidden="true" />
                      <span>{t('chatBody.code.showRemaining', { count: totalLines - maxLines })}</span>
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </div>
      </div>

    </div>
  );
};

export default CodeBlock;
