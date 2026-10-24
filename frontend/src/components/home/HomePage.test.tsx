import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { fetchPromptExamplesMock, preloadChatMessageListMock } = vi.hoisted(() => ({
  fetchPromptExamplesMock: vi.fn(),
  preloadChatMessageListMock: vi.fn(),
}));

vi.mock('@/lib/api/prompts', () => ({
  fetchPromptExamples: fetchPromptExamplesMock,
}));

vi.mock('@/components/lazy/preloaders', () => ({
  preloadChatMessageList: preloadChatMessageListMock,
}));

vi.mock('next/image', () => ({
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  default: function MockNextImage({ alt = '', fill, sizes, ...props }: any) {
    // 测试环境只需要普通 img 承载 next/image props
    // eslint-disable-next-line @next/next/no-img-element
    return <img alt={alt} {...props} />;
  },
}));

import HomePage from './HomePage';

const REGISTERED_FEATURES = {
  model_selection: true,
  file_upload: true,
  image_generation: true,
  map: true,
  knowledge_base: true,
  mcp_tools: true,
  personalization: true,
};

const GUEST_FEATURES = Object.fromEntries(
  Object.keys(REGISTERED_FEATURES).map((key) => [key, false]),
) as typeof REGISTERED_FEATURES;

function showcaseLabels(): string[] {
  return screen.getAllByTestId('showcase-card').map(
    (card) => within(card).getByRole('button').getAttribute('aria-label') ?? '',
  );
}

describe('HomePage', () => {
  beforeEach(() => {
    preloadChatMessageListMock.mockReset();
    fetchPromptExamplesMock.mockReset();
    fetchPromptExamplesMock.mockResolvedValue({
      examples: [
        { question: '今天有哪些值得关注的 AI 进展？', category: 'tech' },
        { question: '最近有哪些重要科技新闻？', category: 'news' },
        { question: '如何安排一次高质量复盘？', category: 'general' },
        { question: '帮我设计一套用户访谈提纲', category: 'general' },
        { question: '分析本周产品数据中的异常趋势', category: 'tech' },
        { question: '比较三个技术方案的成本和风险', category: 'tech' },
        { question: '整理一份项目复盘行动清单', category: 'general' },
        { question: '总结今天的重要行业新闻', category: 'news' },
        { question: '设计一个新用户引导流程', category: 'general' },
        { question: '解释分布式系统中的背压机制', category: 'tech' },
        { question: '制定下季度产品目标和里程碑', category: 'general' },
        { question: '分析一份竞品报告的关键差异', category: 'general' },
        { question: '把会议纪要整理成责任清单', category: 'general' },
        { question: '规划一周的高效学习安排', category: 'general' },
        { question: '审查接口设计中的兼容性风险', category: 'tech' },
        { question: '为发布公告调整表达和语气', category: 'general' },
      ],
      refreshed_at: '2026-07-14T09:00:00+08:00',
    });
  });

  it('点击生图卡片只填入同款提示词，不直接创建会话', () => {
    const onSelectPrompt = vi.fn();

    render(<HomePage onSelectPrompt={onSelectPrompt} features={REGISTERED_FEATURES} />);

    fireEvent.click(screen.getByRole('button', { name: '做同款：水彩插画' }));

    expect(onSelectPrompt).toHaveBeenCalledTimes(1);
    expect(onSelectPrompt).toHaveBeenCalledWith(expect.stringContaining('橘猫'));
  });

  it('生图卡片只在确认开通生图后展示', () => {
    const { rerender } = render(<HomePage onSelectPrompt={vi.fn()} />);
    expect(screen.queryByTestId('image-showcase')).toBeNull();

    rerender(<HomePage onSelectPrompt={vi.fn()} features={GUEST_FEATURES} />);
    expect(screen.queryByTestId('image-showcase')).toBeNull();

    rerender(<HomePage onSelectPrompt={vi.fn()} features={REGISTERED_FEATURES} />);
    expect(screen.getAllByTestId('showcase-card')).toHaveLength(4);
    expect(screen.getByRole('region', { name: '今日灵感' })).toBeInTheDocument();
  });

  it('生图卡片按波浪翻牌自动换到下一组，轮完一圈回到起点', async () => {
    vi.useFakeTimers();
    try {
      render(<HomePage onSelectPrompt={vi.fn()} features={REGISTERED_FEATURES} />);
      const firstPage = showcaseLabels();
      expect(firstPage).toHaveLength(4);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(12_001);
      });
      const cards = screen.getAllByTestId('showcase-card');
      expect(cards[0]).toHaveStyle({ transform: 'rotateX(90deg)' });
      expect(cards.map((card) => card.style.transitionDelay)).toEqual([
        '0ms',
        '80ms',
        '160ms',
        '240ms',
      ]);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });
      const secondPage = showcaseLabels();
      expect(secondPage).toHaveLength(4);
      expect(secondPage.some((label) => firstPage.includes(label))).toBe(false);
      expect(screen.getAllByTestId('showcase-card')[0]).toHaveStyle({ transform: 'rotateX(0deg)' });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(12_001);
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(700);
      });
      expect(showcaseLabels()).toEqual(firstPage);
    } finally {
      vi.useRealTimers();
    }
  });

  it('鼠标停在生图卡片上时暂停轮换，移开后继续', async () => {
    vi.useFakeTimers();
    try {
      render(<HomePage onSelectPrompt={vi.fn()} features={REGISTERED_FEATURES} />);
      const firstPage = showcaseLabels();

      fireEvent.mouseEnter(screen.getByTestId('image-showcase'));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(showcaseLabels()).toEqual(firstPage);

      fireEvent.mouseLeave(screen.getByTestId('image-showcase'));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(12_701);
      });
      expect(showcaseLabels()).not.toEqual(firstPage);
    } finally {
      vi.useRealTimers();
    }
  });

  it('首屏立即稳定展示两行灵感', () => {
    fetchPromptExamplesMock.mockReturnValue(new Promise(() => {}));

    render(<HomePage onSelectPrompt={vi.fn()} />);

    const inspirationRegion = screen.getByRole('region', { name: '今日灵感' });
    expect(screen.getByTestId('inspiration-cloud')).toHaveAttribute('data-row-count', '2');
    expect(within(inspirationRegion).getAllByRole('button').length).toBeGreaterThan(4);
  });

  it('需要额外功能的灵感只在用户有该功能时展示', async () => {
    fetchPromptExamplesMock.mockResolvedValue({
      examples: [
        { question: '帮我画一只戴墨镜的柴犬', category: 'image', requires: 'image_generation' },
        { question: '最近 AI 圈有什么大新闻', category: 'news', requires: null },
      ],
      refreshed_at: null,
    });
    const guestFeatures = {
      model_selection: false,
      file_upload: false,
      image_generation: false,
      map: false,
      knowledge_base: false,
      mcp_tools: false,
      personalization: false,
    };

    const { rerender } = render(<HomePage onSelectPrompt={vi.fn()} features={guestFeatures} />);

    expect(await screen.findByRole('button', { name: '最近 AI 圈有什么大新闻' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '帮我画一只戴墨镜的柴犬' })).not.toBeInTheDocument();

    rerender(<HomePage onSelectPrompt={vi.fn()} features={{ ...guestFeatures, image_generation: true }} />);

    expect(await screen.findByRole('button', { name: '帮我画一只戴墨镜的柴犬' })).toBeInTheDocument();
  });

  it('把远端动态问题作为低干扰的今日灵感展示', async () => {
    const onSelectPrompt = vi.fn();
    render(<HomePage onSelectPrompt={onSelectPrompt} />);

    expect(await screen.findByText('今日灵感')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: '今天有哪些值得关注的 AI 进展？' }));

    expect(onSelectPrompt).toHaveBeenCalledWith('今天有哪些值得关注的 AI 进展？');
  });

  it('今日灵感按宽度填充两行并继续自动轮换', async () => {
    vi.useFakeTimers();
    try {
      render(<HomePage onSelectPrompt={vi.fn()} />);

      await act(async () => {
        await Promise.resolve();
      });

      const inspirationRegion = screen.getByRole('region', { name: '今日灵感' });
      const inspirationCloud = screen.getByTestId('inspiration-cloud');
      const beforeRotation = within(inspirationRegion).getAllByRole('button').map(
        (button) => button.textContent,
      );
      expect(inspirationCloud).toHaveAttribute('data-row-count', '2');
      expect(beforeRotation.length).toBeGreaterThan(4);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(16000);
      });

      const afterRotation = within(inspirationRegion).getAllByRole('button').map(
        (button) => button.textContent,
      );
      expect(afterRotation.length).toBeGreaterThan(4);
      expect(afterRotation).not.toEqual(beforeRotation);
    } finally {
      vi.useRealTimers();
    }
  });

  it('从首页预加载对话消息列表代码块', async () => {
    render(<HomePage onSelectPrompt={vi.fn()} />);

    await waitFor(() => {
      expect(preloadChatMessageListMock).toHaveBeenCalledTimes(1);
    });
  });
});
