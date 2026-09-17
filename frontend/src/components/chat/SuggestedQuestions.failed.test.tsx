/**
 * 推荐问题生成失败后的可恢复性（验收报告发现：4/6 失败且页面无提示、无重试入口）。
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SuggestedQuestions from './SuggestedQuestions';

vi.mock('@/redux/hooks', () => ({
  useAppSelector: (selector: (state: unknown) => unknown) =>
    selector({ auth: { isAuthenticated: true } }),
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

describe('SuggestedQuestions 失败态', () => {
  it('生成失败时给出提示与重试入口', () => {
    const onRefresh = vi.fn();
    render(
      <SuggestedQuestions
        questions={[]}
        isLoading={false}
        status="failed"
        onSelectQuestion={vi.fn()}
        onRefresh={onRefresh}
      />
    );

    const retry = screen.getByRole('button', { name: /重试/ });
    fireEvent.click(retry);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('没有重试回调时不渲染空壳', () => {
    const { container } = render(
      <SuggestedQuestions
        questions={[]}
        isLoading={false}
        status="failed"
        onSelectQuestion={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('未失败且无问题时保持完全不渲染', () => {
    const { container } = render(
      <SuggestedQuestions
        questions={[]}
        isLoading={false}
        status="ready"
        onSelectQuestion={vi.fn()}
        onRefresh={vi.fn()}
      />
    );
    expect(container).toBeEmptyDOMElement();
  });
});
