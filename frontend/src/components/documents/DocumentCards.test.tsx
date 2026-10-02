import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { DocumentBlock } from '@/types/conversation';

const { getDocumentMock, getDocumentContentMock } = vi.hoisted(() => ({
  getDocumentMock: vi.fn(),
  getDocumentContentMock: vi.fn(),
}));

vi.mock('@/lib/api/documents', () => ({
  getDocument: getDocumentMock,
  getDocumentContent: getDocumentContentMock,
}));

import DocumentCards, { latestDocumentBlocks } from './DocumentCards';

function block(overrides: Partial<DocumentBlock> = {}): DocumentBlock {
  return {
    type: 'document',
    id: 'blk-1',
    schema_version: 1,
    document_id: 'doc-1',
    version: 1,
    title: '香港三天两夜攻略',
    format: 'markdown',
    operation: 'created',
    change_summary: null,
    char_count: 3200,
    ...overrides,
  };
}

const VERSIONS = [
  { version: 1, title: '香港三天两夜攻略', change_summary: null, char_count: 3200, created_at: null },
  { version: 2, title: '香港三天两夜攻略', change_summary: 'D1 改去中环', char_count: 3300, created_at: null },
];

function contentFor(version: number) {
  return {
    document_id: 'doc-1',
    version,
    title: '香港三天两夜攻略',
    format: 'markdown',
    content: version === 1
      ? '# 攻略\n\n::::tabs\n:::tab[D1]\n去油麻地\n:::\n:::tab[D2]\n去太平山\n:::\n::::\n'
      : '# 攻略\n\n:::warning\n去中环\n:::\n',
    change_summary: version === 2 ? 'D1 改去中环' : null,
    sources: [{ kind: 'weather', label: '香港天气', provider: '高德地图', fetched_at: '2026-10-02T01:30:00Z' }],
    created_at: null,
  };
}

describe('DocumentCards', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh-CN');
  });

  beforeEach(() => {
    getDocumentMock.mockReset().mockResolvedValue({
      id: 'doc-1',
      conversation_id: 'conv-1',
      title: '香港三天两夜攻略',
      format: 'markdown',
      current_version: 2,
      versions: VERSIONS,
      created_at: null,
      updated_at: null,
    });
    getDocumentContentMock.mockReset().mockImplementation((_id: string, version: number) => (
      Promise.resolve(contentFor(version))
    ));
  });

  it('keeps only the latest card per document', () => {
    const latest = latestDocumentBlocks([
      block(),
      block({ id: 'blk-2', document_id: 'doc-2', title: '清单' }),
      block({ id: 'blk-3', version: 2, operation: 'edited', change_summary: 'D1 改去中环' }),
    ]);
    expect(latest.map(item => [item.document_id, item.version])).toEqual([['doc-2', 1], ['doc-1', 2]]);
  });

  it('opens the panel at the card version, renders directives and sources, and switches versions', async () => {
    const user = userEvent.setup();
    render(<DocumentCards blocks={[block()]} />);
    expect(screen.getByText('已创建 · v1 · 3,200 字')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /香港三天两夜攻略/ }));
    const panel = await screen.findByTestId('document-panel');
    expect(getDocumentContentMock).toHaveBeenCalledWith('doc-1', 1, expect.any(AbortSignal));
    expect(await within(panel).findByText('去油麻地')).toBeInTheDocument();
    expect(within(panel).queryByText('去太平山')).not.toBeInTheDocument();
    await user.click(within(panel).getByRole('tab', { name: 'D2' }));
    expect(within(panel).getByText('去太平山')).toBeInTheDocument();
    expect(within(panel).getByTestId('document-sources')).toHaveTextContent('天气：香港天气 · 高德地图（查询于 2026/10/02 09:30）');

    fireEvent.change(within(panel).getByLabelText('文档版本'), { target: { value: '2' } });
    expect(await within(panel).findByText('去中环')).toBeInTheDocument();
    expect(within(panel).getByText('注意')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('document-panel')).not.toBeInTheDocument());
  });

  it('shows a retry when loading fails', async () => {
    getDocumentContentMock.mockRejectedValueOnce(new Error('boom'));
    render(<DocumentCards blocks={[block()]} />);
    fireEvent.click(screen.getByRole('button', { name: /香港三天两夜攻略/ }));
    fireEvent.click(await screen.findByRole('button', { name: '重试' }));
    expect(await screen.findByText('去油麻地')).toBeInTheDocument();
  });

  it('does not render raw html from document content', async () => {
    getDocumentContentMock.mockResolvedValue({ ...contentFor(1), content: '正文<script>alert(1)</script><b>粗</b>' });
    render(<DocumentCards blocks={[block()]} />);
    fireEvent.click(screen.getByRole('button', { name: /香港三天两夜攻略/ }));
    const markdown = await screen.findByTestId('document-markdown');
    await waitFor(() => expect(markdown).toHaveTextContent('正文'));
    expect(markdown.querySelector('script')).toBeNull();
    expect(markdown.querySelector('b')).toBeNull();
  });

  it('downloads a standalone html with every tab expanded and the system sources', async () => {
    const code = [...Array.from({ length: 60 }, (_, i) => `const line${i} = ${i};`), '<script>literal</script>', '代码末尾'].join('\n');
    getDocumentContentMock.mockResolvedValue({ ...contentFor(1), content: [
      contentFor(1).content,
      ':::tabs', ':::tab[代码]', '```ts', code, '```', ':::',
      ':::tab[说明]', ':::tip[提示标题]', '提示内容', ':::',
      ':::tabs', ':::tab[内层]', '| 名称 | 数值 |', '| --- | --- |', '| 项目 | 10 |', ':::', ':::', ':::', ':::',
    ].join('\n') });
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((value: Blob) => {
      blobs.push(value);
      return 'blob:document';
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    render(<DocumentCards blocks={[block()]} />);
    fireEvent.click(screen.getByRole('button', { name: /香港三天两夜攻略/ }));
    await screen.findByText('去油麻地');
    fireEvent.click(screen.getByRole('button', { name: /HTML/ }));

    await waitFor(() => expect(blobs).toHaveLength(1));
    const html = await blobs[0].text();
    expect(html).toContain('<!doctype html>');
    expect(html).toContain('去油麻地');
    expect(html).toContain('去太平山');
    expect(html).toContain('fdoc-sources');
    expect(html).not.toContain('<script');
    const exported = new DOMParser().parseFromString(html, 'text/html');
    expect(exported.querySelector('pre')?.textContent).toBe(`${code}\n`);
    expect(exported.body.textContent).toContain('提示内容');
    expect(exported.querySelector('table')?.textContent?.replace(/\s+/g, '')).toContain('项目10');
    expect(exported.querySelector('button, [role="tablist"]')).toBeNull();
    clickSpy.mockRestore();
  });
});
