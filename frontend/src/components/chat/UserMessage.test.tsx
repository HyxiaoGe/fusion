import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';

import type { ContentBlock, FileBlock, Message } from '@/types/conversation';
import { getFileUrl } from '@/lib/api/files';

import UserMessage from './UserMessage';

vi.mock('@/lib/api/files', () => ({
  getFileUrl: vi.fn(),
}));

const getFileUrlMock = vi.mocked(getFileUrl);
const toastMock = vi.fn();
vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ toast: toastMock }),
}));
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'user-1',
    role: 'user',
    content: [{ type: 'text', id: 'text-1', text: '请总结这份材料' }],
    timestamp: 1,
    ...overrides,
  };
}

function renderUserMessage({
  message = makeMessage(),
  blocksToRender = message.content,
  messageText = '请总结这份材料',
  onRetry = vi.fn(),
  onEdit = vi.fn(),
  onViewImage = vi.fn(),
}: {
  message?: Message;
  blocksToRender?: ContentBlock[];
  messageText?: string;
  onRetry?: (messageId: string) => void;
  onEdit?: (messageId: string, content: string) => void;
  onViewImage?: (block: FileBlock) => void;
} = {}) {
  render(
    <UserMessage
      message={message}
      blocksToRender={blocksToRender}
      messageText={messageText}
      onRetry={onRetry}
      onEdit={onEdit}
      onViewImage={onViewImage}
    />,
  );

  return { onRetry, onEdit, onViewImage };
}

describe('UserMessage', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    getFileUrlMock.mockReset();
    toastMock.mockReset();
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true,
    });
  });

  afterEach(() => {
    if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
    else Reflect.deleteProperty(navigator, 'clipboard');
  });

  it('渲染普通用户文本', () => {
    renderUserMessage();

    expect(screen.getByText('请总结这份材料')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument();
  });

  it('用户文本保留换行、长串与 Markdown 字符的纯文本语义', () => {
    const text = `第一行\n**原样保留**\nhttps://example.com/${'a'.repeat(300)}`;
    renderUserMessage({ messageText: text });

    const bubble = screen.getByLabelText('用户消息内容');

    expect(bubble.textContent).toBe(text);
    expect(bubble.querySelector('strong')).toBeNull();
    expect(bubble.querySelector('a')).toBeNull();
  });

  it('复制用户原文保留换行和 Markdown，且不会触发编辑或重新发送', async () => {
    const text = '第一行\n**原样保留**\n最后一行\n';
    const { onEdit, onRetry } = renderUserMessage({ messageText: text });
    const copyButton = screen.getByRole('button', { name: '复制', exact: true });
    copyButton.focus();
    fireEvent.click(copyButton);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(text);
    await waitFor(() => expect(copyButton).toHaveAccessibleName('已复制'));
    expect(copyButton).toHaveFocus();
    expect(onEdit).not.toHaveBeenCalled();
    expect(onRetry).not.toHaveBeenCalled();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('复制失败显示错误反馈且可以重新复制', async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error('blocked'));
    renderUserMessage();
    fireEvent.click(screen.getByRole('button', { name: '复制', exact: true }));
    await waitFor(() => expect(toastMock).toHaveBeenCalledWith({ message: '复制失败，请重试', type: 'error' }));
    fireEvent.click(screen.getByRole('button', { name: '复制', exact: true }));
    await screen.findByRole('button', { name: '已复制', exact: true });
  });

  it('仅附件且没有文本时不显示无效的复制入口', () => {
    const file: FileBlock = { type: 'file', id: 'file-1', file_id: 'pdf-1', filename: '说明.pdf', mime_type: 'application/pdf' };
    renderUserMessage({ blocksToRender: [file], messageText: '' });
    expect(screen.getByText('说明.pdf')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制', exact: true })).toBeNull();
  });

  it('渲染 failed 状态提示和重新发送操作', () => {
    const message = makeMessage({ status: 'failed' });
    const { onRetry } = renderUserMessage({ message });

    expect(screen.getByText('发送失败，请重新发送')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新发送' }));

    expect(onRetry).toHaveBeenCalledWith('user-1');
  });

  it('编辑态按 Ctrl+Enter 保存并退出编辑', () => {
    const { onEdit } = renderUserMessage();

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));

    const textarea = screen.getByPlaceholderText('编辑您的消息...');
    fireEvent.change(textarea, { target: { value: '更新后的问题' } });
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true });

    expect(onEdit).toHaveBeenCalledWith('user-1', '更新后的问题');
    expect(screen.queryByPlaceholderText('编辑您的消息...')).toBeNull();
    expect(screen.getByText('请总结这份材料')).toBeInTheDocument();
  });

  it('编辑态按 Esc 取消并恢复原内容', () => {
    const { onEdit } = renderUserMessage();

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));

    const textarea = screen.getByPlaceholderText('编辑您的消息...');
    fireEvent.change(textarea, { target: { value: '不会保存的内容' } });
    fireEvent.keyDown(textarea, { key: 'Escape' });

    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText('编辑您的消息...')).toBeNull();
    expect(screen.getByText('请总结这份材料')).toBeInTheDocument();
  });

  it('点击图片文件 block 时交给父组件查看', () => {
    const imageBlock: FileBlock = {
      type: 'file',
      id: 'file-1',
      file_id: 'img-1',
      filename: 'diagram.png',
      mime_type: 'image/png',
      thumbnail_url: 'https://example.com/diagram.png',
    };
    const { onViewImage } = renderUserMessage({
      blocksToRender: [imageBlock, { type: 'text', id: 'text-1', text: '看这张图' }],
      messageText: '看这张图',
    });

    fireEvent.click(screen.getByAltText('diagram.png'));

    expect(onViewImage).toHaveBeenCalledWith(imageBlock);
  });

  it('图片失败卡片不可点击打开查看器', async () => {
    getFileUrlMock.mockRejectedValueOnce(new Error('文件不存在'));
    const imageBlock: FileBlock = {
      type: 'file',
      id: 'file-1',
      file_id: 'img-1',
      filename: 'diagram.png',
      mime_type: 'image/png',
      thumbnail_url: '/api/files/img-1/content?variant=thumbnail&token=expired',
    };
    const { onViewImage } = renderUserMessage({
      blocksToRender: [imageBlock, { type: 'text', id: 'text-1', text: '看这张图' }],
      messageText: '看这张图',
    });

    fireEvent.error(screen.getByAltText('diagram.png'));
    const failedCard = await screen.findByLabelText('diagram.png 加载失败');

    fireEvent.click(failedCard);

    expect(onViewImage).not.toHaveBeenCalled();
  });
});
