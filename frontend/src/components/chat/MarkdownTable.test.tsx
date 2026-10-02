import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import MarkdownTable from './MarkdownTable';

let resize: () => void;
const disconnect = vi.fn();

beforeEach(async () => {
  await i18n.changeLanguage('zh-CN');
  disconnect.mockClear();
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resize = callback; }
    observe() {}
    disconnect = disconnect;
  });
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

function contents(extraRow = false) {
  return <MarkdownTable><thead><tr><th>地点</th><th>说明</th></tr></thead><tbody><tr><td>西湖</td><td>步行</td></tr>{extraRow ? <tr><td>灵隐寺</td><td>预约</td></tr> : null}</tbody></MarkdownTable>;
}

function dimensions(element: HTMLElement, horizontal: boolean, vertical: boolean) {
  Object.defineProperties(element, {
    clientWidth: { configurable: true, value: 400 },
    scrollWidth: { configurable: true, value: horizontal ? 800 : 400 },
    clientHeight: { configurable: true, value: 448 },
    scrollHeight: { configurable: true, value: vertical ? 900 : 200 },
  });
  act(() => resize());
}

describe('MarkdownTable', () => {
  it('仅溢出时加入键盘入口和方向提示，滚动到边缘后更新提示层', () => {
    render(contents());
    const region = screen.getByRole('region', { name: '回答表格' });
    expect(region).not.toHaveAttribute('tabindex');
    expect(region).not.toHaveAttribute('aria-describedby');
    dimensions(region, true, false);
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toHaveAccessibleDescription('可左右滚动查看完整表格');
    expect(region.parentElement).toHaveAttribute('data-right', 'true');
    region.scrollLeft = 400;
    fireEvent.scroll(region);
    expect(region.parentElement).toHaveAttribute('data-right', 'false');
    expect(region.parentElement).toHaveAttribute('data-left', 'true');
    dimensions(region, false, false);
    expect(region).not.toHaveAttribute('tabindex');
    expect(screen.queryByText('可左右滚动查看完整表格')).toBeNull();
  });

  it('流式新增行保留同一滚动层和表格语义，并更新双向提示', () => {
    const { rerender, unmount } = render(contents());
    const region = screen.getByRole('region', { name: '回答表格' });
    dimensions(region, true, false);
    region.scrollLeft = 100;
    rerender(contents(true));
    dimensions(region, true, true);
    expect(screen.getByRole('region', { name: '回答表格' })).toBe(region);
    expect(region.scrollLeft).toBe(100);
    expect(screen.getByRole('columnheader', { name: '地点' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '灵隐寺' })).toBeInTheDocument();
    expect(screen.getByText('可上下、左右滚动查看表格，表头保持可见')).toBeInTheDocument();
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
