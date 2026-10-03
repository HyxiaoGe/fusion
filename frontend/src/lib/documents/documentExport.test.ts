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


describe('document source provider presentation', () => {
  const sources = [
    { kind: 'weather' as const, label: '杭州天气', provider: 'amap', fetched_at: '2026-10-02T01:30:00Z' },
    { kind: 'place' as const, label: '西湖', provider: '高德地图' },
    { kind: 'web' as const, label: 'Firecrawl 官网原文', provider: 'firecrawl', url: 'https://example.com/firecrawl' },
    { kind: 'url' as const, label: '高德地图服务说明', provider: 'internal-provider', url: 'https://example.com/amap' },
  ];

  it.each([
    ['zh-CN', '高德地图', '天气', '数据来源'],
    ['en-US', 'AMap', 'Weather', 'Data sources'],
  ])('localizes known providers and hides raw identifiers in %s Markdown', (locale, provider, kind, heading) => {
    const translate = i18n.getFixedT(locale);
    const markdown = buildMarkdownExport({ ...document, content: '正文中的 Firecrawl 和高德地图品牌原文\n', sources }, translate, locale);
    expect(markdown).toContain(`## ${heading}`);
    expect(markdown).toContain(`${kind}：杭州天气 · ${provider}`);
    expect(markdown).toContain(`西湖 · ${provider}`);
    expect(markdown).not.toContain(' · amap');
    expect(markdown).not.toContain(' · firecrawl');
    expect(markdown).not.toContain('internal-provider');
    expect(markdown).toContain('正文中的 Firecrawl 和高德地图品牌原文');
    expect(markdown).toContain('[Firecrawl 官网原文](https://example.com/firecrawl)');
    expect(markdown).toContain('[高德地图服务说明](https://example.com/amap)');
    expect(markdown).toMatch(/2026[\/]10[\/]02|10[\/]02[\/]2026/);
  });
});
