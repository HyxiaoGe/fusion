import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import GeneratedImages from './GeneratedImages';
import { collectPendingImageGenerations, imageDisplayWidth, type PendingImageGeneration } from './generatedImageModel';
import i18n from '@/lib/i18n';
import { getFileUrl } from '@/lib/api/files';
import type { AgentRunState, ToolCallState } from '@/types/agentRun';
import type { GeneratedImageBlock } from '@/types/conversation';

vi.mock('@/lib/api/files', () => ({
  getFileUrl: vi.fn(),
}));

const getFileUrlMock = vi.mocked(getFileUrl);

const block: GeneratedImageBlock = {
  type: 'generated_image',
  id: 'blk-img',
  schema_version: 1,
  provider: 'image-service',
  file_id: 'file-1',
  mime_type: 'image/jpeg',
  width: 848,
  height: 1264,
  prompt: '一只红狐狸',
  aspect_ratio: '2:3',
  model: 'doubao-seedream-4-5',
};

function call(overrides: Partial<ToolCallState>): ToolCallState {
  return {
    toolCallId: 'call-1',
    toolName: 'generate_image',
    arguments: { prompt: '一只红狐狸', aspect_ratio: '2:3' },
    status: 'running',
    startedAt: 1_000,
    ...overrides,
  };
}

function runWith(toolCalls: ToolCallState[]): AgentRunState {
  return {
    runId: 'run-1',
    messageId: 'msg-1',
    status: 'running',
    config: { maxSteps: 8, maxToolCalls: 16, timeoutS: 300 },
    totalSteps: 1,
    totalToolCalls: toolCalls.length,
    steps: [{ stepId: 'step-1', stepNumber: 1, status: 'running', toolCalls, contentBlockIds: [], startedAt: 1_000 }],
    lastSequence: 1,
  } as AgentRunState;
}

describe('GeneratedImages', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh-CN');
    getFileUrlMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders nothing without blocks or pending generations', () => {
    const { container } = render(<GeneratedImages blocks={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the providing model, keeps the prompt collapsed and opens the viewer on click', async () => {
    getFileUrlMock.mockResolvedValue('/api/files/file-1/content?variant=processed&token=t');

    render(<GeneratedImages blocks={[block]} />);

    const image = await screen.findByAltText('一只红狐狸');
    expect(getFileUrlMock).toHaveBeenCalledWith('file-1', 'processed');
    expect(image).toHaveAttribute('src', '/api/files/file-1/content?variant=processed&token=t');
    expect(screen.getByText('本次生图由 豆包 Seedream 4.5 提供')).toBeInTheDocument();
    expect(screen.queryByText('一只红狐狸')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看描述' }));
    expect(screen.getByText('一只红狐狸')).toBeInTheDocument();

    fireEvent.load(image);
    expect(image).toHaveAttribute('data-loaded', 'true');
    fireEvent.click(image);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('asks for another image with the same description when regenerating', async () => {
    getFileUrlMock.mockResolvedValue('/img');
    const onRegenerate = vi.fn();

    render(<GeneratedImages blocks={[block]} onRegenerate={onRegenerate} />);
    fireEvent.click(await screen.findByRole('button', { name: '重新生成' }));

    expect(onRegenerate).toHaveBeenCalledWith('用同样的描述再生成一张');
  });

  it('hides regenerate when the conversation cannot continue from this message', async () => {
    getFileUrlMock.mockResolvedValue('/img');
    render(<GeneratedImages blocks={[block]} />);
    await screen.findByAltText('一只红狐狸');
    expect(screen.queryByRole('button', { name: '重新生成' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下载' })).toBeInTheDocument();
  });

  it('shows a dot placeholder at the requested ratio with elapsed seconds while generating', () => {
    vi.useFakeTimers();
    vi.setSystemTime(13_400);
    const pending: PendingImageGeneration[] = [
      { toolCallId: 'call-1', status: 'running', aspectRatio: '2:3', startedAt: 1_000, errorCode: null },
    ];

    render(<GeneratedImages blocks={[]} pending={pending} />);

    const card = screen.getByTestId('generated-image-pending');
    expect(card).toHaveStyle({ width: `${imageDisplayWidth(2, 3)}px` });
    expect(screen.getByRole('status')).toHaveTextContent('正在生成图片');
    expect(screen.getByText('12 秒')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByText('14 秒')).toBeInTheDocument();
  });

  it('explains a failed generation in place', () => {
    const pending: PendingImageGeneration[] = [
      { toolCallId: 'call-1', status: 'failed', aspectRatio: '1:1', startedAt: 1_000, completedAt: 9_000, errorCode: 'content_filtered' },
      { toolCallId: 'call-2', status: 'failed', aspectRatio: '1:1', startedAt: 1_000, completedAt: 9_000, errorCode: 'upstream_error' },
    ];

    render(<GeneratedImages blocks={[]} pending={pending} />);

    expect(screen.getByText('图片内容未通过模型审核')).toBeInTheDocument();
    expect(screen.getByText('图片生成失败')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('collectPendingImageGenerations', () => {
  it('keeps a call pending until its image block arrives', () => {
    const running = runWith([call({})]);
    expect(collectPendingImageGenerations(running, [])).toMatchObject([
      { toolCallId: 'call-1', status: 'running', aspectRatio: '2:3', startedAt: 1_000 },
    ]);

    const succeeded = runWith([call({
      status: 'success',
      resultSummary: { kind: 'generated_image', truncated: false, file_id: 'file-1' } as never,
    })]);
    expect(collectPendingImageGenerations(succeeded, [])).toHaveLength(1);
    expect(collectPendingImageGenerations(succeeded, [block])).toEqual([]);
  });

  it('reports failures with their error code and ignores other tools', () => {
    const run = runWith([
      call({ status: 'failed', resultSummary: { kind: 'generated_image', truncated: false, error_code: 'content_filtered' } as never }),
      call({ toolCallId: 'call-2', toolName: 'web_search' }),
    ]);
    expect(collectPendingImageGenerations(run, [])).toMatchObject([
      { toolCallId: 'call-1', status: 'failed', errorCode: 'content_filtered' },
    ]);
    expect(collectPendingImageGenerations(null, [])).toEqual([]);
  });
});
