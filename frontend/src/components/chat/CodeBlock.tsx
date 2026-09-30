'use client';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Check, ClipboardCopy, FileText, Hash, ChevronDown, ChevronUp } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import hljs from 'highlight.js';
import styles from './CodeBlock.module.css';

interface CodeBlockProps {
  language: string;
  value: string;
  showLineNumbers?: boolean;
  className?: string;
  maxLines?: number; // 最大显示行数，超过则可折叠
}

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
  text: 'Plain Text',
  plaintext: 'Plain Text',
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
  const [copied, setCopied] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('复制失败:', error);
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

  return (
    <div className={cn(styles.block, className)} data-code-block="true">
      {/* 代码块头部 */}
      <div className={styles.header}>
        <div className={styles.metadata}>
          <FileText aria-hidden="true" />
          <span className={styles.language}>
            {getDisplayLanguage(language)}
          </span>
          {showLineNumbers && (
            <div className={styles.lineCount}>
              <Hash aria-hidden="true" />
              <span>
                {isCollapsed && shouldShowCollapse 
                  ? `${maxLines}/${totalLines} 行` 
                  : `${totalLines} 行`
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
              title={isCollapsed ? "展开代码" : "折叠代码"}
              aria-label={isCollapsed ? "展开代码" : "折叠代码"}
              aria-expanded={!isCollapsed}
            >
              {isCollapsed ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronUp className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className={styles.iconButton}
          onClick={handleCopy}
          title="复制代码"
        >
          {copied ? (
            <Check className={styles.success} />
          ) : (
            <ClipboardCopy className="h-3 w-3" />
          )}
          <span className="sr-only">复制代码</span>
        </Button>
      </div>

      {/* 代码内容 */}
      <div className={styles.scroll}>
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
                    >
                      <ChevronDown className="h-4 w-4 mr-2" />
                      显示剩余 {totalLines - maxLines} 行代码
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </div>
      </div>

      {/* 复制成功提示 */}
      {copied && (
        <div className={styles.copied} role="status">
          已复制
        </div>
      )}
    </div>
  );
};

export default CodeBlock;
