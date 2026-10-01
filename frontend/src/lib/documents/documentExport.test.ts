import { beforeAll, describe, expect, it } from 'vitest';
import i18n from '@/lib/i18n';
import {
  buildDocumentFilename,
  buildHtmlExport,
  buildMarkdownExport,
  buildSourcesMarkdown,
} from './documentExport';

const document = {
  document_id: 'doc-1',
  version: 2,
  title: '香港攻略',
  format: 'markdown' as const,
  content: '# 香港攻略\n\n正文\n',
  change_summary: null,
  created_at: null,
  sources: [
    { kind: 'weather' as const, label: '香港 10-16 至 10-18 天气', provider: '高德地图', fetched_at: '2026-10-02T01:30:00Z' },
    { kind: 'url' as const, label: '港铁票价', url: 'https://example.com/fare' },
  ],
};

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options);

describe('document export', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh-CN');
  });

  it('appends system sources after the body', () => {
    const markdown = buildMarkdownExport(document, t);
    expect(markdown.startsWith('# 香港攻略\n\n正文\n\n---\n\n## 数据来源')).toBe(true);
    expect(markdown).toContain('- 天气：香港 10-16 至 10-18 天气 · 高德地图（查询于 2026/10/02 09:30）');
    expect(markdown).toContain('- 网页：[港铁票价](https://example.com/fare)');
    expect(buildSourcesMarkdown([], t)).toBe('');
    expect(buildMarkdownExport({ ...document, sources: [] }, t)).toBe('# 香港攻略\n\n正文\n');
  });

  it('builds safe filenames and escapes the html title', () => {
    expect(buildDocumentFilename('香港/攻略: v1?', 3, 'html')).toBe('香港 攻略 v1-v3.html');
    const html = buildHtmlExport('<b>攻略</b>', '<div class="fdoc">x</div>');
    expect(html).toContain('<title>&lt;b&gt;攻略&lt;/b&gt;</title>');
    expect(html).toContain('<div class="fdoc">x</div>');
    expect(html).not.toContain('<script');
  });
});
