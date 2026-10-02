import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it } from 'vitest';
import i18n from '@/lib/i18n';
import DocumentMarkdown from './DocumentMarkdown';

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
});
