import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import i18n from '@/lib/i18n';
import { buildHtmlExport } from '@/lib/documents/documentExport';
import type { DocumentSource } from '@/types/document';
import DocumentSources from './DocumentSources';

const sources: DocumentSource[] = [
  { kind: 'weather', label: '杭州天气', provider: 'amap', fetched_at: '2026-10-02T01:30:00Z' },
  { kind: 'place', label: '西湖', provider: '高德地图' },
  { kind: 'web', label: 'Firecrawl 官网原文', provider: 'firecrawl', url: 'https://example.com/firecrawl' },
  { kind: 'url', label: '高德地图服务说明', provider: 'internal-provider', url: 'https://example.com/amap' },
];

describe('DocumentSources', () => {
  it.each([
    ['zh-CN', '高德地图', '天气', '数据来源'],
    ['en-US', 'AMap', 'Weather', 'Data sources'],
  ])('uses the same user-facing source names on the page and in %s HTML', async (locale, provider, kind, heading) => {
    await i18n.changeLanguage(locale);
    render(<DocumentSources sources={sources} />);
    const section = screen.getByTestId('document-sources');
    expect(within(section).getByRole('heading', { name: heading })).toBeInTheDocument();
    expect(section).toHaveTextContent(`${kind}：杭州天气 · ${provider}`);
    expect(section).toHaveTextContent(`西湖 · ${provider}`);
    expect(section).not.toHaveTextContent(' · amap');
    expect(section).not.toHaveTextContent(' · firecrawl');
    expect(section).not.toHaveTextContent('internal-provider');
    expect(within(section).getByRole('link', { name: 'Firecrawl 官网原文' })).toHaveAttribute('href', 'https://example.com/firecrawl');
    expect(within(section).getByRole('link', { name: '高德地图服务说明' })).toHaveAttribute('href', 'https://example.com/amap');

    const html = buildHtmlExport('数据来源', renderToStaticMarkup(<><p>正文中的 Firecrawl 和高德地图品牌原文</p><DocumentSources sources={sources} /></>), locale);
    const exported = new DOMParser().parseFromString(html, 'text/html');
    expect(exported.documentElement.lang).toBe(locale);
    expect(exported.body.textContent).toContain(`${kind}：杭州天气 · ${provider}`);
    expect(exported.body.textContent).toContain(`西湖 · ${provider}`);
    expect(exported.body.textContent).not.toContain(' · amap');
    expect(exported.body.textContent).not.toContain(' · firecrawl');
    expect(exported.body.textContent).not.toContain('internal-provider');
    expect(exported.querySelector('a[href="https://example.com/firecrawl"]')?.textContent).toBe('Firecrawl 官网原文');
    expect(exported.querySelector('a[href="https://example.com/amap"]')?.textContent).toBe('高德地图服务说明');
    expect(exported.body.textContent).toContain('正文中的 Firecrawl 和高德地图品牌原文');
    expect(exported.body.textContent).toMatch(/2026[\/]10[\/]02|10[\/]02[\/]2026/);
  });
});
