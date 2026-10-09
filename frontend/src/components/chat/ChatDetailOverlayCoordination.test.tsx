import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import i18n from '@/lib/i18n';
import type { ContextUsage } from '@/types/conversation';

import AnswerEvidenceSidebar from './AnswerEvidenceSidebar';
import type { AnswerEvidenceSidebarModel } from './answerEvidenceSidebarModel';
import {
  ChatDetailOverlayProvider,
} from './ChatDetailOverlayContext';
import ContextStatus, {
  CONTEXT_STATUS_DEFAULT_OPEN_STORAGE_KEY,
} from './ContextStatus';

const usage: ContextUsage = {
  status: 'within_budget',
  window_tokens: 100_000,
  estimated_tokens_before: 40_000,
  estimated_tokens_after: 40_000,
  actual_prompt_tokens: 40_000,
  removed_turns: 0,
  removed_messages: 0,
  removed_tool_transactions: 0,
  round_index: 1,
};

const evidenceModel: AnswerEvidenceSidebarModel = {
  summary: {
    usedCount: 1,
    candidateCount: 0,
    searchCount: 1,
    urlCount: 0,
    issueCount: 0,
  },
  usedItems: [
    {
      id: 'source-1',
      kind: 'search',
      title: '深圳餐厅来源',
      url: 'https://example.com/shenzhen',
      domain: 'example.com',
      sourceIndex: 0,
    },
  ],
  candidateItems: [],
  issueItems: [],
  searchQueries: ['深圳聚餐'],
  isRenderable: true,
};

function ContextPanel() {
  return <ContextStatus conversationId="chat-overlay" usage={usage} />;
}

function AnswerEvidenceHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        data-chat-detail-overlay-trigger="true"
        onClick={() => setOpen(true)}
      >
        打开回答依据
      </button>
      <AnswerEvidenceSidebar
        model={evidenceModel}
        isOpen={open}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

describe('聊天详情浮层互斥', () => {
  beforeEach(async () => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem(CONTEXT_STATUS_DEFAULT_OPEN_STORAGE_KEY, 'true');
    await i18n.changeLanguage('zh-CN');
  });

  it('回答依据打开时临时隐藏上下文，关闭后恢复且不修改自动展开偏好', async () => {
    const user = userEvent.setup();
    render(
      <ChatDetailOverlayProvider>
        <ContextPanel />
        <AnswerEvidenceHarness />
      </ChatDetailOverlayProvider>,
    );

    await user.click(screen.getByRole('button', { name: '打开回答依据' }));

    expect(screen.getByRole('dialog', { name: '回答依据' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '上下文状态' })).not.toBeInTheDocument();
    expect(localStorage.getItem(CONTEXT_STATUS_DEFAULT_OPEN_STORAGE_KEY)).toBe('true');

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.getByRole('dialog', { name: '上下文状态' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: '回答完成后自动展开' })).toBeChecked();
  });

  it('用户已临时关闭上下文时，打开再关闭详情浮层不会重新展开', () => {
    render(
      <ChatDetailOverlayProvider>
        <ContextPanel />
        <AnswerEvidenceHarness />
      </ChatDetailOverlayProvider>,
    );

    fireEvent.click(screen.getByTestId('context-status-trigger'));
    expect(screen.queryByRole('dialog', { name: '上下文状态' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '打开回答依据' }));
    fireEvent.click(screen.getByRole('button', { name: '关闭回答依据' }));

    expect(screen.queryByRole('dialog', { name: '上下文状态' })).not.toBeInTheDocument();
    expect(localStorage.getItem(CONTEXT_STATUS_DEFAULT_OPEN_STORAGE_KEY)).toBe('true');
  });

  it('详情侧栏所在消息卸载时清理登记并恢复上下文', () => {
    function UnmountHarness() {
      const [mounted, setMounted] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setMounted(false)}>卸载回答消息</button>
          {mounted ? (
            <AnswerEvidenceSidebar
              model={evidenceModel}
              isOpen
              onClose={() => undefined}
            />
          ) : null}
        </>
      );
    }

    render(
      <ChatDetailOverlayProvider>
        <ContextPanel />
        <UnmountHarness />
      </ChatDetailOverlayProvider>,
    );

    expect(screen.queryByRole('dialog', { name: '上下文状态' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '卸载回答消息' }));
    expect(screen.getByRole('dialog', { name: '上下文状态' })).toBeInTheDocument();
    expect(localStorage.getItem(CONTEXT_STATUS_DEFAULT_OPEN_STORAGE_KEY)).toBe('true');
  });

  it('回答依据侧栏隔离消息间距并由视口上下边界约束高度', () => {
    render(
      <div className="space-y-1">
        <AnswerEvidenceSidebar
          model={evidenceModel}
          isOpen
          onClose={() => undefined}
        />
      </div>,
    );

    const dialog = screen.getByRole('dialog', { name: '回答依据' });
    expect(dialog.parentElement).toBe(document.body);
    expect(screen.getByRole('button', { name: '关闭回答依据背景' }).parentElement).toBe(document.body);
    expect(dialog).toHaveClass('inset-y-0');
    expect(dialog).not.toHaveClass('h-full');
  });
});
