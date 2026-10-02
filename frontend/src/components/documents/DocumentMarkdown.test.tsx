import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it } from 'vitest';
import i18n from '@/lib/i18n';
import DocumentMarkdown from './DocumentMarkdown';
import { buildDocumentHighlights } from '@/lib/documents/documentDiffHighlights';

const tabs = (labels: string[], close = true) => [
  ':::tabs',
  ...labels.flatMap(label => [`:::tab[${label}]`, `${label}的正文`, ':::']),
  ...(close ? [':::'] : []),
].join('\n');

const nested = [
  '::::::tabs',
  ':::::tab[外层一]',
  tabs(['内层一', '内层二']),
  ':::::',
  ':::::tab[外层二]',
  '外层二的正文',
  ':::::',
  '::::::',
].join('\n');

describe('文档富内容', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });

  it('方向键循环切换、Home/End 定位，Tab 进入关联面板', async () => {
    const user = userEvent.setup();
    render(<DocumentMarkdown content={tabs(['甲', '乙', '丙'])} />);
    const first = screen.getByRole('tab', { name: '甲' });
    const last = screen.getByRole('tab', { name: '丙' });
    await user.tab();
    expect(first).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(last).toHaveFocus();
    expect(last).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('丙的正文');
    await user.keyboard('{ArrowRight}');
    expect(first).toHaveFocus();
    await user.keyboard('{End}');
    expect(last).toHaveFocus();
    await user.keyboard('{Home}');
    expect(first).toHaveFocus();
    await user.keyboard('{ArrowRight}');
    const selected = screen.getByRole('tab', { name: '乙' });
    expect(selected).toHaveAttribute('tabindex', '0');
    expect(first).toHaveAttribute('tabindex', '-1');
    const panel = screen.getByRole('tabpanel');
    expect(selected).toHaveAttribute('aria-controls', panel.id);
    expect(panel).toHaveAttribute('aria-labelledby', selected.id);
    await user.tab();
    expect(panel).toHaveFocus();
  });

  it('多组和嵌套标签页的关联 ID 独立，内层切换不会切换外层', async () => {
    const user = userEvent.setup();
    const { container } = render(<DocumentMarkdown content={`${nested}\n\n${tabs(['甲', '乙'])}`} />);
    const ids = [...container.querySelectorAll('[id]')].map(node => node.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const tab of screen.getAllByRole('tab')) {
      const panel = document.getElementById(tab.getAttribute('aria-controls')!);
      expect(panel).toHaveAttribute('aria-labelledby', tab.id);
    }
    const inner = screen.getByRole('tab', { name: '内层一' });
    inner.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: '内层二' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '外层一' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('内层二的正文')).toBeInTheDocument();
  });

  it('流式未闭合标签页增减时保留选择并显示有效正文', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<DocumentMarkdown content={tabs(['甲', '乙'], false)} />);
    await user.click(screen.getByRole('tab', { name: '乙' }));
    rerender(<DocumentMarkdown content={tabs(['甲', '乙', '丙'], false)} />);
    expect(screen.getByRole('tab', { name: '乙' })).toHaveAttribute('aria-selected', 'true');
    rerender(<DocumentMarkdown content={tabs(['甲'], false)} />);
    expect(screen.getByRole('tab', { name: '甲' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('甲的正文');
    rerender(<DocumentMarkdown content={`${tabs(['甲'])}\n\n尾段`} />);
    expect(screen.getByText('尾段')).toBeInTheDocument();
  });

  it('无语言长代码完整保留，代码中的指令和 HTML 仍是字面文本', () => {
    const code = [':::tip', '<script>alert(1)</script>', ...Array.from({ length: 60 }, (_, i) => `第${i + 1}行`), '代码末尾'].join('\n');
    const { container } = render(<DocumentMarkdown content={`\`\`\`\n${code}\n\`\`\``} />);
    const pre = container.querySelector('pre')!;
    expect(pre.textContent).toBe(`${code}\n`);
    expect(pre).toHaveAttribute('tabindex', '0');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('.fdoc-callout')).toBeNull();
    expect(screen.getByText('纯文本')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制代码' })).toBeInTheDocument();
  });

  it('静态导出展开嵌套标签页及全部代码，移除交互控件', () => {
    const html = renderToStaticMarkup(<DocumentMarkdown content={`${nested}\n\n\`\`\`ts\nconst full = true;\n\`\`\``} expandTabs />);
    const container = document.createElement('div');
    container.innerHTML = html;
    expect(container.textContent).toContain('内层一的正文');
    expect(container.textContent).toContain('内层二的正文');
    expect(container.textContent).toContain('外层二的正文');
    expect(container.querySelector('pre')?.textContent).toBe('const full = true;\n');
    expect(container.querySelector('button, [role="tablist"], script, pre[tabindex]')).toBeNull();
  });

  it('提示块、统计和含嵌套列表的时间线在未闭合增量更新中保持内容', () => {
    const content = ':::warning[出发前]\n带好证件\n:::\n:::stats[预算]\n- 总额: ¥600\n:::\n:::timeline[上午]\n1. 到站\n   - 取票\n2. 游览';
    const { rerender } = render(<DocumentMarkdown content={content} />);
    expect(screen.getByText('出发前')).toBeInTheDocument();
    expect(screen.getByText('¥600')).toBeInTheDocument();
    expect(screen.getByText('取票')).toBeInTheDocument();
    act(() => { rerender(<DocumentMarkdown content={`${content}\n3. 午餐\n:::\n\n完成`} />); });
    expect(screen.getByText('午餐')).toBeInTheDocument();
    expect(screen.getByText('完成')).toBeInTheDocument();
  });

  it('差异高亮按原文位置投影到 CRLF 嵌套容器，重复文字不会误标', () => {
    const before = '相同正文\r\n\r\n::::tabs\r\n:::tab[甲]\r\n相同正文\r\n:::\r\n:::tab[乙]\r\n相同正文\r\n:::\r\n::::\r\n';
    const after = before.replace(':::tab[乙]\r\n相同正文', ':::tab[乙]\r\n不同正文');
    const { container } = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} expandTabs />);
    expect(container.querySelectorAll('mark')).toHaveLength(1);
    expect(container.querySelector('mark')).toHaveTextContent('不');
    expect(container.querySelector('mark')).toHaveAttribute('data-document-change', 'modified');
    expect(screen.getAllByText('相同正文')).toHaveLength(2);
  });

  it('高亮代码与转义字符时保留字面内容，普通阅读不产生任何标记', () => {
    const before = '```html\n<script>alert(1)</script>\n```\n\nA &amp; B\n';
    const after = before.replace('alert(1)', 'alert(2)').replace('&amp;', '&lt;');
    const view = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} expandTabs />);
    expect(view.container.querySelector('pre')?.textContent).toBe('<script>alert(2)</script>\n');
    expect(view.container.querySelector('div[data-document-change="modified"]')).toBeInTheDocument();
    expect(view.container.querySelector('script')).toBeNull();
    expect(view.container.querySelector('mark')).toHaveTextContent('A < B');
    view.rerender(<DocumentMarkdown content={after} expandTabs />);
    expect(view.container.querySelector('[data-document-change]')).toBeNull();
    expect(view.container.querySelector('pre')?.textContent).toBe('<script>alert(2)</script>\n');
  });

  it('正文与提示块标题同名时，源位置仍落在实际改动的正文上', () => {
    const before = ':::tip[西溪]\n西湖\n:::\n';
    const after = ':::tip[西溪]\n西溪\n:::\n';
    const { container } = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} expandTabs />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
    expect(marks[0]).toHaveTextContent('溪');
    expect(marks[0].closest('.fdoc-callout-body')).toBeInTheDocument();
    expect(container.querySelector('.fdoc-callout-heading mark')).toBeNull();
  });

  it('统计字段去掉内部强调后，只标记对应的数值和标签，重复数值不误标', () => {
    const before = ':::stats[预算概览]\n- **总**预算: **¥1,000**\n- 已使用: ¥1,000\n- 备用金: ¥500\n- 天数：3 天\n:::\n';
    const after = ':::stats[费用概览]\n- **总**费用: **¥1,200**\n- 已使用: ¥1,000\n- 天数：3 天\n- 交通额度: ¥200\n:::\n';
    const highlights = buildDocumentHighlights(before, after);
    const view = render(<DocumentMarkdown content={after} highlights={highlights.after} expandTabs />);
    const cards = view.container.querySelectorAll('.fdoc-stat');
    expect(cards).toHaveLength(4);
    expect(cards[0].querySelector('.fdoc-stat-label mark')).toHaveTextContent('费用');
    expect(cards[0].querySelector('.fdoc-stat-value mark')).toHaveTextContent('2');
    expect(cards[0].textContent).toBe('¥1,200总费用');
    expect(cards[1].querySelector('mark')).toBeNull();
    expect(cards[2].querySelector('mark')).toBeNull();
    expect(cards[3].querySelectorAll('mark')).toHaveLength(2);
    cards[3].querySelectorAll('mark').forEach(mark => expect(mark).toHaveAttribute('data-document-change', 'added'));
    expect(view.container.querySelector('.fdoc-block-label mark')).toHaveTextContent('费用');
    view.rerender(<DocumentMarkdown content={before} highlights={highlights.before} expandTabs />);
    view.container.querySelectorAll('.fdoc-stat')[2].querySelectorAll('mark').forEach(mark => expect(mark).toHaveAttribute('data-document-change', 'removed'));
  });

  it('提示、时间线和嵌套标签页标题按真实源位置高亮，同名正文保持普通文字', () => {
    const before = '::::tabs\r\n:::tab[西湖]\r\n:::tip[西湖]\r\n西湖\r\n:::\r\n:::timeline[西湖]\r\n- 到站\r\n:::\r\n:::\r\n::::\r\n';
    const after = before.replaceAll('[西湖]', '[西溪]');
    const { container } = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} expandTabs />);
    for (const selector of ['.fdoc-tab-label', '.fdoc-callout-label', '.fdoc-block-label']) {
      const mark = container.querySelector(`${selector} mark`);
      expect(mark).toHaveTextContent('溪');
      expect(mark).toHaveAttribute('data-document-change', 'modified');
    }
    expect(container.querySelector('.fdoc-callout-body mark')).toBeNull();
    expect(screen.getByText('西湖')).toBeInTheDocument();
  });

  it('未闭合嵌套统计块仍映射到真实字段，完整原文中的容器标签不会误标', () => {
    const before = ':::stats[费用]\n- 费用: 100\n:::tip[费用]\n- 天数: 3\n';
    const after = before.replace('天数: 3', '天数: 4');
    const { container } = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} expandTabs />);
    expect(container.querySelectorAll('mark')).toHaveLength(1);
    expect(container.querySelector('.fdoc-stat-value mark')).toHaveTextContent('4');
    expect(container.querySelector('.fdoc-block-label mark')).toBeNull();
  });

  it('交互标签标题高亮不会影响键盘切换；普通阅读和静态导出不残留标记', async () => {
    const user = userEvent.setup();
    const before = tabs(['甲', '乙']);
    const after = before.replace(':::tab[乙]', ':::tab[丙]');
    const view = render(<DocumentMarkdown content={after} highlights={buildDocumentHighlights(before, after).after} />);
    const tab = screen.getByRole('tab', { name: '丙' });
    expect(tab.querySelector('mark')).toHaveTextContent('丙');
    screen.getByRole('tab', { name: '甲' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(tab).toHaveFocus();
    expect(tab).toHaveAttribute('aria-selected', 'true');
    view.rerender(<DocumentMarkdown content={after} />);
    expect(view.container.querySelector('[data-document-change]')).toBeNull();
    const html = renderToStaticMarkup(<DocumentMarkdown content={after} expandTabs />);
    expect(html).not.toContain('data-document-change');
    expect(html).toContain('丙');
  });
});
