import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import DocumentCodeBlock from './DocumentCodeBlock';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

describe('文档代码复制', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh-CN'); });
  afterEach(() => {
    vi.restoreAllMocks();
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
  });

  function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, get: () => ({ writeText }) });
    return writeText;
  }

  it('复制完整长代码，显示成功，内容更新后恢复反馈', async () => {
    const value = `${'整行代码\n'.repeat(100)}末尾\n`;
    const writeText = clipboard();
    const { rerender } = render(<DocumentCodeBlock value={value} language="ts" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(await screen.findByText('已复制')).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(value);
    rerender(<DocumentCodeBlock value={`${value}增量\n`} language="ts" />);
    expect(screen.getByText('复制')).toBeInTheDocument();
  });

  it('旧内容的异步复制结束不会覆盖新内容反馈，卸载后也不创建计时器', async () => {
    let resolveCopy!: () => void;
    clipboard(vi.fn(() => new Promise<void>(resolve => { resolveCopy = resolve; })));
    const timer = vi.spyOn(globalThis, 'setTimeout');
    const { rerender, unmount } = render(<DocumentCodeBlock value="旧内容" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(screen.getByRole('button', { name: '复制代码' })).toBeDisabled();
    rerender(<DocumentCodeBlock value="新内容" />);
    await act(async () => { resolveCopy(); });
    expect(screen.queryByText('已复制')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制代码' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    unmount();
    timer.mockClear();
    await act(async () => { resolveCopy(); });
    expect(timer).not.toHaveBeenCalled();
  });

  it('剪贴板拒绝后说明手动复制，允许重试', async () => {
    const writeText = clipboard(vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValue(undefined));
    render(<DocumentCodeBlock value="完整代码" />);
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('复制失败，请选择代码手动复制。');
    fireEvent.click(screen.getByRole('button', { name: '复制代码' }));
    expect(await screen.findByText('已复制')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(2);
  });
});
