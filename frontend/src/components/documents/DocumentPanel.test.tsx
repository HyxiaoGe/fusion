import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { DocumentVersionContent } from '@/types/document';
import { readDocumentReadingPosition } from '@/lib/documents/documentReadingStorage';

const { getDocumentMock, getContentMock, identity } = vi.hoisted(() => ({
  getDocumentMock: vi.fn(), getContentMock: vi.fn(), identity: { value: 'user-a' },
}));
vi.mock('@/lib/api/documents', () => ({ getDocument: getDocumentMock, getDocumentContent: getContentMock }));
vi.mock('@/redux/hooks', () => ({ useAppSelector: () => identity.value }));
import DocumentPanel from './DocumentPanel';

function contentFor(id: string, version: number): DocumentVersionContent {
  return { document_id: id, version, title: `文档 ${id}`, content: `# 第${version}版\n\n${id} 的正文`,
    format: 'markdown', change_summary: null, sources: [], created_at: null };
}

describe('文档侧栏请求归属与阅读恢复', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
  beforeEach(() => {
    window.sessionStorage.clear();
    identity.value = 'user-a';
    getDocumentMock.mockReset().mockImplementation((id: string) => Promise.resolve({
      id, title: `文档 ${id}`, current_version: 2, versions: [
        { version: 1, title: `文档 ${id}`, change_summary: null },
        { version: 2, title: `文档 ${id}`, change_summary: null },
      ],
    }));
    getContentMock.mockReset().mockImplementation((id: string, version: number) => Promise.resolve(contentFor(id, version)));
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const body = this.closest<HTMLElement>('[data-testid="document-reading-body"]');
      const top = this.dataset.testid === 'document-reading-body' ? 100 : 124 - (body?.scrollTop ?? 0);
      return { top, bottom: top + 24, left: 0, right: 600, width: 600, height: 24, x: 0, y: top, toJSON: () => ({}) };
    });
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it('切换版本与关闭重开分别恢复原版本的最后位置', async () => {
    const user = userEvent.setup();
    const props = { documentId: 'doc', initialVersion: 1, isOpen: true, onClose: vi.fn() };
    const view = render(<DocumentPanel {...props} />);
    let body = await screen.findByTestId('document-reading-body');
    body.scrollTop = 320;
    fireEvent.scroll(body);
    await user.selectOptions(screen.getByLabelText('文档版本'), '2');
    expect(await screen.findByRole('heading', { name: '第2版' })).toBeInTheDocument();
    body = screen.getByTestId('document-reading-body');
    expect(body.scrollTop).toBe(0);
    body.scrollTop = 680;
    fireEvent.scroll(body);
    await user.selectOptions(screen.getByLabelText('文档版本'), '1');
    await screen.findByRole('heading', { name: '第1版' });
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(320);
    view.rerender(<DocumentPanel {...props} isOpen={false} />);
    view.rerender(<DocumentPanel {...props} />);
    await waitFor(() => expect(screen.getByTestId('document-reading-body').scrollTop).toBe(320));
    expect(readDocumentReadingPosition('user-a', 'doc', 2)?.scrollTop).toBe(680);
  });

  it('旧版本请求迟到时不覆盖新版本正文与导出内容', async () => {
    let resolveOld!: (value: DocumentVersionContent) => void;
    getContentMock.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    render(<DocumentPanel documentId="doc" initialVersion={1} isOpen onClose={vi.fn()} />);
    const select = await screen.findByLabelText('文档版本');
    fireEvent.change(select, { target: { value: '2' } });
    await screen.findByRole('heading', { name: '第2版' });
    await act(async () => resolveOld(contentFor('doc', 1)));
    expect(screen.queryByRole('heading', { name: '第1版' })).toBeNull();
    expect(select).toHaveValue('2');
    const blobs: Blob[] = [];
    Object.assign(URL, { createObjectURL: vi.fn((blob: Blob) => { blobs.push(blob); return 'blob:doc'; }), revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Markdown' }));
    expect(await blobs[0].text()).toContain('# 第2版');
    expect(await blobs[0].text()).not.toContain('# 第1版');
    click.mockRestore();
  });

  it('换文档或账号后不展示前一个 owner 的正文，也不复用阅读位置', async () => {
    const props = { documentId: 'doc-a', initialVersion: 1, isOpen: true, onClose: vi.fn() };
    const view = render(<DocumentPanel {...props} />);
    const body = await screen.findByTestId('document-reading-body');
    body.scrollTop = 420;
    fireEvent.scroll(body);
    view.rerender(<DocumentPanel {...props} documentId="doc-b" />);
    expect(screen.queryByText('doc-a 的正文')).toBeNull();
    await screen.findByText('doc-b 的正文');
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(0);
    identity.value = 'user-b';
    view.rerender(<DocumentPanel {...props} />);
    expect(screen.queryByText('doc-b 的正文')).toBeNull();
    await screen.findByText('doc-a 的正文');
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(0);
    expect(readDocumentReadingPosition('user-a', 'doc-a', 1)?.scrollTop).toBe(420);
  });

  it('比较后返回正文保留当前版本阅读位置，关闭重开回到正常阅读', async () => {
    const user = userEvent.setup();
    const props = { documentId: 'doc', initialVersion: 2, isOpen: true, onClose: vi.fn() };
    const view = render(<DocumentPanel {...props} />);
    const body = await screen.findByTestId('document-reading-body');
    body.scrollTop = 420;
    fireEvent.scroll(body);
    await user.click(screen.getByRole('button', { name: '查看差异' }));
    await screen.findByTestId('document-comparison-view');
    expect(screen.queryByTestId('document-reading-body')).toBeNull();
    expect(readDocumentReadingPosition('user-a', 'doc', 2)?.scrollTop).toBe(420);
    await user.click(screen.getByRole('button', { name: '返回正文' }));
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(420);
    await user.click(screen.getByRole('button', { name: '查看差异' }));
    view.rerender(<DocumentPanel {...props} isOpen={false} />);
    view.rerender(<DocumentPanel {...props} />);
    await screen.findByTestId('document-reading-body');
    expect(screen.queryByTestId('document-comparison-view')).toBeNull();
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(420);
  });

  it('首版没有可比较旧版本，单版本不显示入口', async () => {
    const props = { documentId: 'doc', initialVersion: 1, isOpen: true, onClose: vi.fn() };
    const view = render(<DocumentPanel {...props} />);
    await screen.findByTestId('document-reading-body');
    expect(screen.getByRole('button', { name: '查看差异' })).toBeDisabled();
    getDocumentMock.mockResolvedValueOnce({ id: 'single', title: '单版文档', current_version: 1, versions: [{ version: 1, title: '单版文档' }] });
    view.rerender(<DocumentPanel {...props} documentId="single" />);
    await screen.findByRole('heading', { name: '文档 single' });
    expect(screen.queryByRole('button', { name: '查看差异' })).toBeNull();
  });

  it('比较过程中切换当前版本不展示旧差异，导出仍是当前正文', async () => {
    const user = userEvent.setup();
    render(<DocumentPanel documentId="doc" initialVersion={2} isOpen onClose={vi.fn()} />);
    await screen.findByTestId('document-reading-body');
    await user.click(screen.getByRole('button', { name: '查看差异' }));
    await screen.findByText('v1 → v2');
    await user.selectOptions(screen.getByLabelText('文档版本'), '1');
    await screen.findByRole('heading', { name: '第1版' });
    expect(screen.queryByTestId('document-comparison-view')).toBeNull();
    const blobs: Blob[] = [];
    Object.assign(URL, { createObjectURL: vi.fn((blob: Blob) => { blobs.push(blob); return 'blob:doc'; }), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Markdown' }));
    expect(await blobs[0].text()).toContain('# 第1版');
    expect(await blobs[0].text()).not.toContain('修改前');
  });
});
