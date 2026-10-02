import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import MarkdownRenderer from './MarkdownRenderer';
import { ChatDetailOverlayProvider, useChatDetailOverlayRegistration } from './ChatDetailOverlayContext';

beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
beforeEach(() => {
  // jsdom 没有布局；为浮层的真实“锚点可见”判断提供视口和引用几何。
  vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(1024);
  vi.spyOn(document.documentElement, 'clientHeight', 'get').mockReturnValue(768);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 40, y: 40, top: 40, left: 40, right: 58, bottom: 58, width: 18, height: 18, toJSON: () => ({}) });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
const sources = [
  { title: '第二来源', url: 'https://two.example.com/article', citation_index: 55, snippet: '另一份摘要' },
  { title: '架构说明', url: 'https://www.one.example.com/doc', citation_index: 42, snippet: '原始摘要 <script>不执行</script>' },
];

function Overlay({ open }: { open: boolean }) { useChatDetailOverlayRegistration(open); return null; }

describe('引用就近预览', () => {
  it('悬停延迟打开，移到浮层可继续读，离开关闭；只创建一个定位器', async () => {
    vi.useFakeTimers();
    const onOpen = vi.fn();
    render(<MarkdownRenderer content="参考[42][55]" sources={sources} onCitationClick={onOpen} />);
    const chip = screen.getByRole('button', { name: /参考资料 42/ });
    fireEvent.pointerOver(chip);
    expect(screen.queryByRole('tooltip')).toBeNull();
    await act(async () => { vi.advanceTimersByTime(250); });
    const preview = screen.getByRole('tooltip');
    expect(preview).toHaveTextContent('架构说明');
    expect(preview).toHaveTextContent('one.example.com');
    expect(preview).toHaveTextContent(sources[1].snippet);
    expect(preview.querySelector('script')).toBeNull();
    expect(document.querySelectorAll('[data-radix-popper-content-wrapper]')).toHaveLength(1);
    expect(onOpen).not.toHaveBeenCalled();
    fireEvent.pointerOut(chip, { relatedTarget: preview });
    fireEvent.pointerOver(preview, { relatedTarget: chip });
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(screen.getByRole('tooltip')).toBe(preview);
    fireEvent.pointerOut(preview);
    await act(async () => { vi.advanceTimersByTime(150); });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('键盘聚焦即时预览、Escape 收起且焦点保持，Enter 仍打开正确来源', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<MarkdownRenderer content={'| 参数 |\n| --- |\n| 架构[42] 缺失[99] |'} sources={sources} onCitationClick={onOpen} />);
    const chip = within(screen.getByRole('cell')).getByRole('button', { name: /参考资料 42/ });
    act(() => chip.focus());
    await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent('架构说明'));
    expect(chip.getAttribute('aria-describedby')).toBe(screen.getByRole('tooltip').id);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(chip).toHaveFocus();
    expect(chip).not.toHaveAttribute('aria-describedby');
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.getByRole('cell')).toHaveTextContent('[99]');
    await user.keyboard('{Enter}');
    expect(onOpen).toHaveBeenCalledWith(1);
  });

  it('没有摘要的旧来源与知识库明确降级，知识引用不生成空外链', async () => {
    render(<MarkdownRenderer content="资料[1]" sources={[{ title: '安装手册.md', url: '', kind: 'knowledge' }]} />);
    const chip = screen.getByLabelText('安装手册.md');
    act(() => chip.focus());
    const preview = await screen.findByRole('tooltip');
    expect(preview).toHaveTextContent('知识库');
    expect(preview).toHaveTextContent('此来源暂无摘要。');
    expect(document.querySelector('a[href=""]')).toBeNull();
    expect(preview).not.toHaveTextContent('点击引用编号');
  });

  it('来源替换或正文移除时旧预览立即消失；等待中的悬停不泄漏', async () => {
    vi.useFakeTimers();
    const { rerender, unmount } = render(<MarkdownRenderer content="资料[42]" sources={sources} onCitationClick={vi.fn()} />);
    const chip = screen.getByRole('button');
    fireEvent.pointerOver(chip);
    rerender(<MarkdownRenderer content="资料[42]" sources={[{ ...sources[1], url: 'https://new.example.com' }]} onCitationClick={vi.fn()} />);
    await act(async () => { vi.advanceTimersByTime(250); });
    expect(screen.queryByRole('tooltip')).toBeNull();
    act(() => screen.getByRole('button').focus());
    expect(screen.getByRole('tooltip')).toHaveTextContent('new.example.com');
    rerender(<MarkdownRenderer content="正文已变化" sources={sources} onCitationClick={vi.fn()} />);
    expect(screen.queryByRole('tooltip')).toBeNull();
    unmount();
    await act(async () => { vi.runOnlyPendingTimers(); });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('点击、滚动和完整侧栏打开都收起预览，关闭时不抢新焦点', async () => {
    const onOpen = vi.fn();
    const content = (open: boolean) => <ChatDetailOverlayProvider><Overlay open={open} /><MarkdownRenderer content="资料[42]" sources={sources} onCitationClick={onOpen} /><button>其他操作</button></ChatDetailOverlayProvider>;
    const { rerender } = render(content(false));
    const chip = screen.getByRole('button', { name: /参考资料 42/ });
    act(() => chip.focus());
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.scroll(window);
    expect(screen.queryByRole('tooltip')).toBeNull();
    act(() => screen.getByRole('button', { name: '其他操作' }).focus());
    act(() => chip.focus());
    fireEvent.click(chip);
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(onOpen).toHaveBeenCalledWith(1);
    act(() => screen.getByRole('button', { name: '其他操作' }).focus());
    act(() => chip.focus());
    rerender(content(true));
    expect(screen.queryByRole('tooltip')).toBeNull();
    act(() => screen.getByRole('button', { name: '其他操作' }).focus());
    expect(screen.getByRole('button', { name: '其他操作' })).toHaveFocus();
  });

  it('触屏保留直接点击；多段回答之间同时只显示一个预览', async () => {
    vi.useFakeTimers();
    const onOpen = vi.fn();
    render(<><MarkdownRenderer content="资料[42]" sources={sources} onCitationClick={onOpen} /><MarkdownRenderer content="另一条[55]" sources={sources} onCitationClick={onOpen} /></>);
    const first = screen.getByRole('button', { name: /参考资料 42/ });
    const touch = new Event('pointerover', { bubbles: true });
    Object.defineProperty(touch, 'pointerType', { value: 'touch' });
    fireEvent(first, touch);
    await act(async () => { vi.advanceTimersByTime(300); });
    expect(screen.queryByRole('tooltip')).toBeNull();
    fireEvent.click(first);
    expect(onOpen).toHaveBeenCalledWith(1);
    act(() => first.focus());
    expect(screen.getByRole('tooltip')).toHaveTextContent('架构说明');
    fireEvent.pointerOver(screen.getByRole('button', { name: /参考资料 55/ }));
    await act(async () => { vi.advanceTimersByTime(250); });
    expect(screen.getAllByRole('tooltip')).toHaveLength(1);
    expect(screen.getByRole('tooltip')).toHaveTextContent('第二来源');
  });
});
