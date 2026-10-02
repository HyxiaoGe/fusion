import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import type { DocumentVersionContent } from '@/types/document';

const { getContent } = vi.hoisted(() => ({ getContent: vi.fn() }));
vi.mock('@/lib/api/documents', () => ({ getDocumentContent: getContent }));
import DocumentComparisonView from './DocumentComparisonView';

function snapshot(version: number, overrides: Partial<DocumentVersionContent> = {}): DocumentVersionContent {
  return { document_id: 'doc', version, title: '攻略', content: version === 1 ? '第一天去中环\n\n第二天去西湖\n\n预算 100 元\n' : '第一天去中环\n\n第二天去灵隐寺\n\n预算 200 元\n',
    format: 'markdown', change_summary: '其他不变', sources: [], created_at: null, ...overrides };
}
const olderVersions = [2, 1].map(version => ({ version, title: '攻略', change_summary: null, char_count: 100, created_at: null }));

describe('文档只读版本对比', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
  beforeEach(() => {
    getContent.mockReset().mockImplementation((_id: string, version: number) => Promise.resolve(snapshot(version)));
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })));
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn(function (this: HTMLElement, options: ScrollToOptions) {
      this.scrollTop = options.top ?? 0;
    }) });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const top = this.dataset.changeIndex ? Number(this.dataset.changeIndex) * 400 + 120 : 100;
      return { top, bottom: top + 100, left: 0, right: 600, width: 600, height: 100, x: 0, y: top, toJSON: () => ({}) };
    });
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('默认上一版，可选更早版本，比较真实正文而非修订摘要', async () => {
    const user = userEvent.setup();
    render(<DocumentComparisonView document={snapshot(3)} olderVersions={olderVersions} authIdentity="user" />);
    await screen.findByText('标题和正文没有变化。');
    expect(getContent).toHaveBeenCalledWith('doc', 2, expect.any(AbortSignal));
    await user.selectOptions(screen.getByLabelText('对比版本'), '1');
    const region = await screen.findByRole('region', { name: '变化 1' });
    expect(region).toHaveTextContent('第二天去西湖');
    expect(region).toHaveTextContent('第二天去灵隐寺');
    expect(screen.getByText('v1 → v3')).toBeInTheDocument();
    expect(screen.queryByText('其他不变')).toBeNull();
    expect(screen.queryByText('展开未变内容')).toBeNull();
    expect(screen.queryByText('第一天去中环')).toBeNull();
  });

  it('标题与正文分开计数，键盘导航聚焦具体变化且仅滚动比较区', async () => {
    const user = userEvent.setup();
    render(<DocumentComparisonView document={snapshot(3, { title: '新版攻略' })} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    await screen.findByText('标题修改');
    expect(screen.getByText('1 / 3 处变化')).toBeInTheDocument();
    const next = screen.getByRole('button', { name: '下一处变化' });
    next.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('region', { name: '变化 2' })).toHaveFocus();
    expect(screen.getByText('2 / 3 处变化')).toBeInTheDocument();
    expect(next).toBeEnabled();
    const body = screen.getByRole('region', { name: '文档版本差异' });
    expect(body.scrollTop).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: '上一处变化' }));
    expect(screen.getByRole('region', { name: '变化 1' })).toHaveFocus();
    expect(within(screen.getByRole('region', { name: '文档版本差异' })).getAllByRole('separator')).toHaveLength(2);
  });

  it('文末短变化被滚动上限截短时，导航计数不会退回上一处', async () => {
    render(<DocumentComparisonView document={snapshot(3)} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    await screen.findByRole('region', { name: '变化 2' });
    const body = screen.getByRole('region', { name: '文档版本差异' });
    Object.defineProperties(body, { clientHeight: { value: 500 }, scrollHeight: { value: 800 } });
    (HTMLElement.prototype.scrollTo as ReturnType<typeof vi.fn>).mockImplementation(function (this: HTMLElement, options: ScrollToOptions) {
      this.scrollTop = Math.min(options.top ?? 0, 300);
      fireEvent.scroll(this);
    });
    fireEvent.click(screen.getByRole('button', { name: '下一处变化' }));
    await act(async () => { await new Promise(resolve => requestAnimationFrame(resolve)); });
    expect(body.scrollTop).toBe(300);
    expect(screen.getByText('2 / 2 处变化')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一处变化' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '上一处变化' })).toBeEnabled();
  });

  it('旧基准请求迟到后不会覆盖新比较，失败可重试', async () => {
    let resolveOld!: (value: DocumentVersionContent) => void;
    getContent.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    render(<DocumentComparisonView document={snapshot(3)} olderVersions={olderVersions} authIdentity="user" />);
    const oldSignal = getContent.mock.calls[0][2] as AbortSignal;
    fireEvent.change(screen.getByLabelText('对比版本'), { target: { value: '1' } });
    await screen.findByRole('region', { name: '变化 1' });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld(snapshot(2, { title: '迟到旧版本' })));
    expect(screen.queryByText('迟到旧版本')).toBeNull();
    getContent.mockRejectedValueOnce(new Error('offline'));
    fireEvent.change(screen.getByLabelText('对比版本'), { target: { value: '2' } });
    await screen.findByRole('alert');
    expect(screen.queryByRole('region', { name: '变化 1' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByText('标题和正文没有变化。');
  });

  it('账号变更立即隐藏旧比较，卸载取消新请求', async () => {
    const props = { document: snapshot(3), olderVersions: [olderVersions[1]], authIdentity: 'user-a' };
    const view = render(<DocumentComparisonView {...props} />);
    await screen.findByRole('region', { name: '变化 1' });
    let resolveNew!: (value: DocumentVersionContent) => void;
    getContent.mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve; }));
    view.rerender(<DocumentComparisonView {...props} authIdentity="user-b" />);
    expect(screen.queryByRole('region', { name: '变化 1' })).toBeNull();
    const newSignal = getContent.mock.calls.at(-1)![2] as AbortSignal;
    view.unmount();
    expect(newSignal.aborted).toBe(true);
    await act(async () => resolveNew(snapshot(1)));
    expect(screen.queryByTestId('document-comparison-view')).toBeNull();
  });

  it('错误文档或版本的响应不参与比较', async () => {
    getContent.mockResolvedValueOnce(snapshot(1, { document_id: 'other-doc' }));
    render(<DocumentComparisonView document={snapshot(3)} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    await screen.findByRole('alert');
    expect(screen.queryByRole('region', { name: '变化 1' })).toBeNull();
  });

  it('来源和修订摘要变化不被计入标题正文差异', async () => {
    getContent.mockResolvedValueOnce(snapshot(2, { change_summary: '旧摘要', sources: [{ kind: 'web', label: '旧来源' }] }));
    render(<DocumentComparisonView document={snapshot(3, { change_summary: '新摘要' })} olderVersions={olderVersions} authIdentity="user" />);
    await screen.findByText('标题和正文没有变化。');
    expect(screen.getByRole('button', { name: '上一处变化' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下一处变化' })).toBeDisabled();
    expect(screen.queryByText('旧来源')).toBeNull();
  });

  it('复杂块完整展示前后内容，原文差异保留语法且不会执行 HTML', async () => {
    const before = '::::tabs\n:::tab[表格]\n| 项目 | 预算 |\n| --- | --- |\n| 交通 | 100 |\n:::\n:::tab[代码]\n```js\nconst budget = 100;\n```\n:::\n::::\n';
    getContent.mockResolvedValueOnce(snapshot(1, { content: before }));
    render(<DocumentComparisonView document={snapshot(3, { content: before.replaceAll('100', '200') + '<script>alert(1)</script>\n' })} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    const change = await screen.findByRole('region', { name: '变化 1' });
    expect(within(change).getAllByRole('table')).toHaveLength(2);
    expect(within(change).getByText('const budget = 100;')).toBeInTheDocument();
    expect(within(change).getByText('const budget = 200;')).toBeInTheDocument();
    expect(change.querySelector('script')).toBeNull();
    fireEvent.click(within(change).getByText('查看 Markdown 原文差异'));
    expect(change.querySelector('pre')?.textContent).toContain('const budget');
    expect(change.textContent).toContain('::::tabs');
  });

  it('五天概览表只突出第三天改变的三个单元格，保留其余行列用于定位', async () => {
    const before = '| 天数 | 主题 | 核心产出 | 关键对接人 |\n| --- | --- | --- | --- |\n| Day 1 | 报到与安顿 | 设备可用 | HR |\n| Day 2 | 熟悉业务与流程 | 掌握常用系统 | 业务骨干 |\n| Day 3 | 深入岗位职责 | 明确岗位职责与考核指标、完成首批小任务 | 直属主管 |\n| Day 4 | 上手实操 | 完整任务 | 协作同事 |\n| Day 5 | 复盘与规划 | 周报 | HR |\n';
    const after = before.replace('深入岗位职责', '参加部门新人培训').replace('明确岗位职责与考核指标、完成首批小任务', '完成部门新人培训、整理培训笔记').replace('| 直属主管 |', '| 培训讲师、直属主管 |');
    getContent.mockResolvedValueOnce(snapshot(1, { content: before }));
    render(<DocumentComparisonView document={snapshot(3, { content: after })} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    const change = await screen.findByRole('region', { name: '变化 1' });
    expect(change).toHaveAttribute('data-change-kind', 'modified');
    const tables = within(change).getAllByRole('table');
    expect(tables).toHaveLength(2);
    for (const table of tables) {
      expect(table.querySelectorAll('td[data-document-change="modified"]')).toHaveLength(3);
      const rows = within(table).getAllByRole('row');
      for (const index of [0, 1, 2, 4, 5]) expect(rows[index].querySelector('[data-document-change]')).toBeNull();
      expect(rows[3].querySelector('td')).not.toHaveAttribute('data-document-change');
    }
    expect(tables[1].querySelectorAll('td[data-document-change="modified"]')[2]).toHaveTextContent('培训讲师、直属主管');
  });

  it('列表只给新增午餐标绿，独立删除块标红，原文仍完整保留', async () => {
    const before = '- [ ] 整理工位\n- [ ] 填写反馈\n\n保留的说明。\n\n删除这整段。\n';
    const after = '- [ ] 整理工位\n- [ ] 填写反馈\n- [ ] 和入职引导人约一次午餐\n\n保留的说明。\n';
    getContent.mockResolvedValueOnce(snapshot(1, { content: before }));
    render(<DocumentComparisonView document={snapshot(3, { content: after })} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    const change = await screen.findByRole('region', { name: '变化 1' });
    const marks = [...change.querySelectorAll('[data-testid="document-markdown"] mark')];
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveAttribute('data-document-change', 'added');
    expect(marks[0]).toHaveTextContent('和入职引导人约一次午餐');
    const removed = screen.getByRole('region', { name: '变化 2' });
    expect(removed).toHaveAttribute('data-change-kind', 'removed');
    expect(removed.querySelector('mark')).toHaveAttribute('data-document-change', 'removed');
    fireEvent.click(within(change).getByText('查看 Markdown 原文差异'));
    expect(change.querySelectorAll('pre')[0].textContent).toBe('- [ ] 整理工位\n- [ ] 填写反馈\n');
    expect(change.querySelectorAll('pre')[1].textContent).toBe('- [ ] 整理工位\n- [ ] 填写反馈\n- [ ] 和入职引导人约一次午餐\n');
  });

  it('富内容标题和统计字段直接显示变化，原文与已保存快照保持完整', async () => {
    const before = '::::tabs\n:::tab[原计划]\n:::stats[原预算]\n- 总额: **¥100**\n- 天数: 3\n:::\n:::tip[出发前]\n带好证件\n:::\n:::\n::::\n';
    const after = before.replace('原计划', '新计划').replace('原预算', '新预算').replace('¥100', '¥200').replace('出发前', '到达后');
    getContent.mockResolvedValueOnce(snapshot(1, { content: before }));
    const current = snapshot(3, { content: after });
    render(<DocumentComparisonView document={current} olderVersions={[olderVersions[1]]} authIdentity="user" />);
    const change = await screen.findByRole('region', { name: '变化 1' });
    const rendered = change.querySelectorAll('[data-testid="document-markdown"]');
    for (const side of rendered) {
      expect(side.querySelector('.fdoc-tab-label mark')).toBeInTheDocument();
      expect(side.querySelector('.fdoc-block-label mark')).toBeInTheDocument();
      expect(side.querySelector('.fdoc-callout-label mark')).toBeInTheDocument();
      expect(side.querySelector('.fdoc-stat-value mark')).toBeInTheDocument();
      expect(side.querySelectorAll('.fdoc-stat')[1].querySelector('mark')).toBeNull();
    }
    fireEvent.click(within(change).getByText('查看 Markdown 原文差异'));
    expect(change.querySelectorAll('pre')[0].textContent).toBe(before);
    expect(change.querySelectorAll('pre')[1].textContent).toBe(after);
    expect(current.content).toBe(after);
  });
});
