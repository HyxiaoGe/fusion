'use client';

import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from '@/components/chat/MarkdownRenderer.module.css';

const components: Components = {
  a: ({ href, children }) => href ? (
    <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}</a>
  ) : <span>{children}</span>,
  // 公告正文只展示文字，避免远程图片产生额外请求；链接仍沿 Markdown 默认的安全 URL 规则。
  img: ({ alt }) => <span>{alt}</span>,
  table: ({ children }) => <div className="mb-4 overflow-x-auto"><table className="w-full border-collapse text-sm">{children}</table></div>,
  th: ({ children }) => <th className="border px-3 py-2 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border px-3 py-2 align-top">{children}</td>,
  pre: ({ children }) => <pre className="mb-4 overflow-x-auto rounded-lg bg-muted p-4 text-sm">{children}</pre>,
};

export function ChangelogContent({ content }: { content: string }) {
  return <div className={styles.content}><Markdown remarkPlugins={[remarkGfm]} skipHtml components={components}>{content}</Markdown></div>;
}
