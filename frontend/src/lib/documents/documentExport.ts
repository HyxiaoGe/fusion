import type { DocumentSource, DocumentVersionContent } from '@/types/document';

export type Translate = (key: string, options?: Record<string, unknown>) => string;

export function sourceKindLabel(kind: DocumentSource['kind'], t: Translate): string {
  return t(`documents.sources.kinds.${kind}`);
}

export function formatSourceTime(value: string | null | undefined, locale = 'zh-CN'): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

/** 数据来源由系统按实际工具调用生成，导出时追加在正文之后。 */
export function buildSourcesMarkdown(sources: DocumentSource[], t: Translate, locale = 'zh-CN'): string {
  if (sources.length === 0) return '';
  const lines = sources.map(source => {
    const time = formatSourceTime(source.fetched_at, locale);
    const provider = source.provider ? ` · ${source.provider}` : '';
    const suffix = time ? t('documents.sources.queriedAt', { time }) : '';
    const label = source.url ? `[${escapeMarkdownText(source.label)}](${source.url})` : escapeMarkdownText(source.label);
    return `- ${sourceKindLabel(source.kind, t)}：${label}${provider}${suffix}`;
  });
  return `## ${t('documents.sources.title')}\n\n${lines.join('\n')}\n`;
}

export function buildMarkdownExport(document: DocumentVersionContent, t: Translate, locale = 'zh-CN'): string {
  const body = document.content.trimEnd();
  const sources = buildSourcesMarkdown(document.sources, t, locale);
  return sources ? `${body}\n\n---\n\n${sources}` : `${body}\n`;
}

export function buildDocumentFilename(title: string, version: number, extension: 'md' | 'html'): string {
  const safeTitle = title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80) || 'document';
  return `${safeTitle}-v${version}.${extension}`;
}

/** 单文件 HTML：正文由页面已渲染的静态标记提供，样式内联，不含脚本。 */
export function buildHtmlExport(title: string, bodyHtml: string, lang = 'zh-CN'): string {
  return `<!doctype html>
<html lang="${escapeHtml(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>${EXPORT_CSS}</style>
</head>
<body>
<main class="fdoc-page">
${bodyHtml}
</main>
</body>
</html>
`;
}

export function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement('a');
  link.href = url;
  link.download = filename;
  window.document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeMarkdownText(value: string): string {
  return value.replace(/([\\[\]*_`])/g, '\\$1');
}

const EXPORT_CSS = `
:root { color-scheme: light; --fg: #1f2328; --muted: #656d76; --border: #d8dee4; --subtle: #f6f8fa;
  --info: #2563eb; --info-bg: #eff6ff; --info-border: #bfdbfe;
  --success: #15803d; --success-bg: #f0fdf4; --success-border: #bbf7d0;
  --warn: #b45309; --warn-bg: #fffbeb; --warn-border: #fde68a;
  --teal: #0f766e; --teal-bg: #f0fdfa; --teal-border: #99f6e4; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--fg);
  font: 15px/1.75 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", sans-serif; }
.fdoc-page { max-width: 880px; margin: 0 auto; padding: 40px 24px 64px; }
.fdoc h1 { font-size: 1.75rem; margin: 0 0 1rem; line-height: 1.35; }
.fdoc h2 { font-size: 1.3rem; margin: 2rem 0 0.75rem; line-height: 1.4; }
.fdoc h3 { font-size: 1.1rem; margin: 1.5rem 0 0.6rem; }
.fdoc p, .fdoc ul, .fdoc ol { margin: 0 0 1rem; }
.fdoc ul, .fdoc ol { padding-left: 1.5rem; }
.fdoc li + li { margin-top: 0.35rem; }
.fdoc a { color: var(--info); }
.fdoc hr { border: 0; border-top: 1px solid var(--border); margin: 1.75rem 0; }
.fdoc blockquote { margin: 1.25rem 0; border-left: 3px solid var(--info-border); padding: 0.25rem 0 0.25rem 1rem; color: var(--muted); }
.fdoc code { background: var(--subtle); border: 1px solid var(--border); border-radius: 5px; padding: 0.1em 0.35em; font-size: 0.875em; }
.fdoc-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; margin: 0 0 1rem; }
.fdoc table { width: 100%; border-collapse: collapse; font-size: 14px; }
.fdoc th, .fdoc td { border-bottom: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }
.fdoc th { background: var(--subtle); }
.fdoc-block-label { color: var(--muted); font-size: 13px; font-weight: 600; margin: 0 0 0.5rem; }
.fdoc-callout { margin: 0 0 1rem; padding: 0.75rem 1rem; border: 1px solid var(--info-border); border-left: 3px solid var(--info); border-radius: 10px; background: var(--info-bg); }
.fdoc-callout[data-variant="tip"] { border-color: var(--success-border); border-left-color: var(--success); background: var(--success-bg); }
.fdoc-callout[data-variant="warning"] { border-color: var(--warn-border); border-left-color: var(--warn); background: var(--warn-bg); }
.fdoc-callout[data-variant="price"] { border-color: var(--teal-border); border-left-color: var(--teal); background: var(--teal-bg); }
.fdoc-callout > :last-child { margin-bottom: 0; }
.fdoc-callout-label { font-size: 13px; font-weight: 600; margin: 0 0 0.375rem; color: var(--info); }
.fdoc-callout[data-variant="tip"] .fdoc-callout-label { color: var(--success); }
.fdoc-callout[data-variant="warning"] .fdoc-callout-label { color: var(--warn); }
.fdoc-callout[data-variant="price"] .fdoc-callout-label { color: var(--teal); }
.fdoc-timeline { margin: 0 0 1rem; }
.fdoc-timeline > ul { position: relative; list-style: none; padding-left: 1.25rem; }
.fdoc-timeline > ul::before { content: ""; position: absolute; top: 0.4rem; bottom: 0.4rem; left: 0.3rem; border-left: 2px solid var(--info-border); }
.fdoc-timeline > ul > li { position: relative; padding-left: 0.5rem; }
.fdoc-timeline > ul > li::before { content: ""; position: absolute; top: 0.6rem; left: -1.2rem; width: 0.6rem; height: 0.6rem; border: 2px solid var(--info); border-radius: 999px; background: #fff; }
.fdoc-stats-section { margin: 0 0 1rem; }
.fdoc-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; }
.fdoc-stat { display: flex; flex-direction: column; gap: 0.25rem; border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem 1rem; }
.fdoc-stat-value { font-size: 1.125rem; font-weight: 600; line-height: 1.35; }
.fdoc-stat-label { color: var(--muted); font-size: 12px; }
.fdoc-tab-section { margin: 0 0 1rem; }
.fdoc-tab-label { font-size: 1.125rem; font-weight: 600; margin: 1.5rem 0 0.75rem; padding-bottom: 0.375rem; border-bottom: 1px solid var(--border); }
.fdoc-sources { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 13px; }
.fdoc-sources h2 { font-size: 1rem; color: var(--fg); margin: 0 0 0.5rem; }
.fdoc-sources ul { padding-left: 1.25rem; margin: 0; }
@media print { .fdoc-page { padding: 0; } }
`;
