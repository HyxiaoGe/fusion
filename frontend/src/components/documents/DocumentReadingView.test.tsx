import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { DocumentVersionContent } from '@/types/document';
import { readDocumentReadingPosition, writeDocumentReadingPosition } from '@/lib/documents/documentReadingStorage';
import DocumentReadingView from './DocumentReadingView';

const content = [
  '# 文档', '正文', '## 第一节', '第一节正文', '## 第二节', '第二节正文',
  '## 重复', '第一处', '## 重复', '第二处',
  '```md', '# 代码里的标题', '```', '| 项目 | 描述 |', '| --- | --- |', '| # 表格文本 | 数值 |',
  '::::tabs', ':::tab[方案甲]', '### 甲路径', '甲的内容', ':::',
  ':::tab[方案乙]', '### 乙路径', '乙的内容', ':::', '::::',
].join('\n\n');

function documentFor(overrides: Partial<DocumentVersionContent> = {}): DocumentVersionContent {
  return { document_id: 'doc', version: 1, title: '文档', format: 'markdown', content,
    change_summary: null, sources: [], created_at: null, ...overrides };
}

describe('文档目录与阅读恢复', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const body = this.closest<HTMLElement>('[data-testid="document-reading-body"]');
      const entries = body ? [...body.querySelectorAll('[data-document-heading-key]')] : [];
      const index = entries.indexOf(this);
      const top = this.dataset.testid === 'document-reading-body' ? 100 : 124 + Math.max(0, index) * 300 - (body?.scrollTop ?? 0);
      return { top, bottom: top + 24, left: 0, right: 600, width: 600, height: 24, x: 0, y: top, toJSON: () => ({}) };
    });
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it('目录只收录实际标题，同名标题独立，切换标签页后更新', async () => {
    const user = userEvent.setup();
    render(<DocumentReadingView document={documentFor()} authIdentity="user" />);
    await user.click(screen.getByRole('button', { name: '目录' }));
    let outline = screen.getByRole('navigation', { name: '文档目录' });
    expect(within(outline).queryByText('代码里的标题')).toBeNull();
    expect(within(outline).queryByText('# 表格文本')).toBeNull();
    expect(within(outline).getByRole('button', { name: '甲路径' })).toBeInTheDocument();
    expect(within(outline).queryByRole('button', { name: '乙路径' })).toBeNull();
    const duplicates = within(outline).getAllByRole('button', { name: '重复' });
    expect(duplicates[0].getAttribute('aria-controls')).not.toBe(duplicates[1].getAttribute('aria-controls'));
    await user.click(screen.getByRole('tab', { name: '方案乙' }));
    await user.click(screen.getByRole('button', { name: '目录' }));
    outline = screen.getByRole('navigation', { name: '文档目录' });
    await waitFor(() => expect(within(outline).getByRole('button', { name: '乙路径' })).toBeInTheDocument());
    expect(within(outline).queryByRole('button', { name: '甲路径' })).toBeNull();
  });

  it('键盘跳转只滚动正文并聚焦章节，Escape 先收起目录', async () => {
    const user = userEvent.setup();
    render(<DocumentReadingView document={documentFor()} authIdentity="user" />);
    const toggle = screen.getByRole('button', { name: '目录' });
    toggle.focus();
    await user.keyboard('{Enter}');
    const outline = screen.getByRole('navigation', { name: '文档目录' });
    const target = within(outline).getByRole('button', { name: '第二节' });
    target.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('heading', { name: '第二节' })).toHaveFocus();
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(600);
    expect(screen.getByTestId('document-current-section')).toHaveTextContent('第二节');
    expect(screen.queryByRole('navigation')).toBeNull();
    await user.click(toggle);
    within(screen.getByRole('navigation')).getByRole('button', { name: '第一节' }).focus();
    const escaped = vi.fn();
    document.addEventListener('keydown', escaped);
    await user.keyboard('{Escape}');
    expect(toggle).toHaveFocus();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(escaped).not.toHaveBeenCalled();
    document.removeEventListener('keydown', escaped);
  });

  it('关闭后重新挂载恢复滚动与非默认标签页，不串文档或版本', async () => {
    const user = userEvent.setup();
    const ready = documentFor();
    const first = render(<DocumentReadingView document={ready} authIdentity="user" />);
    await user.click(screen.getByRole('tab', { name: '方案乙' }));
    const body = screen.getByTestId('document-reading-body');
    body.scrollTop = 678;
    fireEvent.scroll(body);
    await waitFor(() => expect(screen.getByTestId('document-current-section')).toHaveTextContent('第二节'));
    first.unmount();
    expect(readDocumentReadingPosition('user', 'doc', 1)?.scrollTop).toBe(678);
    const second = render(<DocumentReadingView document={ready} authIdentity="user" />);
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(678);
    expect(screen.getByRole('tab', { name: '方案乙' })).toHaveAttribute('aria-selected', 'true');
    second.unmount();
    const otherVersion = render(<DocumentReadingView document={documentFor({ version: 2 })} authIdentity="user" />);
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(0);
    expect(screen.getByRole('tab', { name: '方案甲' })).toHaveAttribute('aria-selected', 'true');
    otherVersion.unmount();
    render(<DocumentReadingView document={documentFor({ document_id: 'other-doc' })} authIdentity="user" />);
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(0);
  });

  it('pagehide 保存尚未执行的滚动，换账号后不读前一个账号的坐标', () => {
    const first = render(<DocumentReadingView document={documentFor()} authIdentity="user" />);
    const body = screen.getByTestId('document-reading-body');
    body.scrollTop = 420;
    fireEvent.scroll(body);
    act(() => window.dispatchEvent(new Event('pagehide')));
    expect(readDocumentReadingPosition('user', 'doc', 1)?.scrollTop).toBe(420);
    first.unmount();
    render(<DocumentReadingView document={documentFor()} authIdentity="other-user" />);
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(0);
  });

  it('滚动后立即关闭也保存最后位置，不等待 RAF 或计时器', () => {
    const first = render(<DocumentReadingView document={documentFor()} authIdentity="user" />);
    const body = screen.getByTestId('document-reading-body');
    body.scrollTop = 725;
    fireEvent.scroll(body);
    first.unmount();
    expect(readDocumentReadingPosition('user', 'doc', 1)?.scrollTop).toBe(725);
    render(<DocumentReadingView document={documentFor()} authIdentity="user" />);
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(725);
  });

  it('无标题正文可阅读和恢复，不展示空目录', () => {
    writeDocumentReadingPosition('user', 'doc', 1, { scrollTop: 120, headingKey: null, headingOffset: 0, tabs: {} });
    render(<DocumentReadingView document={documentFor({ content: '没有标题的正文' })} authIdentity="user" />);
    expect(screen.getByText('没有标题的正文')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '目录' })).toBeNull();
    expect(screen.getByTestId('document-reading-body').scrollTop).toBe(120);
  });
});
