import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import i18n from '@/lib/i18n';
import streamSliceReducer, {
  applyDocumentDraftDelta,
  initRun,
  startStream,
  upsertStaticContentBlock,
} from '@/redux/slices/streamSlice';
import DocumentDraftCard from './DocumentDraftCard';

const getDocumentMock = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/documents', () => ({ getDocument: getDocumentMock }));

const DOC_ID = '3f2b8c1e-5d4a-4b6f-9e2d-1a7c8b9d0e1f';

const baseConfig = { maxSteps: 8, maxToolCalls: 20, timeoutS: 300 };

function runningState() {
  let state = streamSliceReducer(undefined, { type: '@@INIT' });
  state = streamSliceReducer(state, startStream({ conversationId: 'c1', messageId: 'm1' }));
  return streamSliceReducer(state, initRun({ conversationId: 'c1', runId: 'r1', messageId: 'm1', config: baseConfig, sequence: 0 }));
}

function draftOf(state: ReturnType<typeof runningState>) {
  return state.byConversation.c1?.currentRun?.documentDraft;
}

describe('文档草稿状态', () => {
  it('按草稿 id 累积标题与正文，新调用覆盖旧草稿', () => {
    let state = runningState();
    const base = { conversationId: 'c1', runId: 'r1', toolName: 'create_document' };
    state = streamSliceReducer(state, applyDocumentDraftDelta({ ...base, draftId: 's1:0' }));
    state = streamSliceReducer(state, applyDocumentDraftDelta({ ...base, draftId: 's1:0', field: 'title', delta: '香港' }));
    state = streamSliceReducer(state, applyDocumentDraftDelta({ ...base, draftId: 's1:0', field: 'content', delta: '# D1' }));
    state = streamSliceReducer(state, applyDocumentDraftDelta({ ...base, draftId: 's1:0', field: 'content', delta: '\n去中环' }));
    expect(draftOf(state)).toEqual({ draftId: 's1:0', toolName: 'create_document', title: '香港', content: '# D1\n去中环', documentId: '' });

    state = streamSliceReducer(state, applyDocumentDraftDelta({ ...base, draftId: 's2:0', field: 'content', delta: '重写' }));
    expect(draftOf(state)).toEqual({ draftId: 's2:0', toolName: 'create_document', title: '', content: '重写', documentId: '' });
  });

  it('忽略不属于当前 run 的草稿片段', () => {
    const state = streamSliceReducer(
      runningState(),
      applyDocumentDraftDelta({ conversationId: 'c1', runId: 'other', draftId: 's1:0', toolName: 'create_document', field: 'content', delta: 'x' }),
    );
    expect(draftOf(state)).toBeUndefined();
  });

  it('正式文档内容块到达后清除草稿', () => {
    let state = streamSliceReducer(
      runningState(),
      applyDocumentDraftDelta({ conversationId: 'c1', runId: 'r1', draftId: 's1:0', toolName: 'create_document', field: 'content', delta: '正文' }),
    );
    state = streamSliceReducer(state, upsertStaticContentBlock({
      conversationId: 'c1',
      runId: 'r1',
      sequence: 1,
      block: {
        type: 'document',
        id: 'blk-doc',
        schema_version: 1,
        document_id: 'doc-1',
        version: 1,
        title: '香港攻略',
        format: 'markdown',
        operation: 'created',
        change_summary: null,
        char_count: 2,
      },
    }));
    expect(draftOf(state)).toBeUndefined();
  });
});

describe('DocumentDraftCard', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh-CN');
  });

  it('撰写中展示标题、字数和实时正文预览', () => {
    render(
      <DocumentDraftCard
        draft={{ draftId: 's1:0', toolName: 'create_document', title: '香港三天两夜攻略', content: '## 第一天\n\n去中环', documentId: '' }}
      />,
    );

    expect(screen.getByText('香港三天两夜攻略')).toBeInTheDocument();
    expect(screen.getByText('正在撰写 · 11 字')).toBeInTheDocument();
    const preview = screen.getByRole('region', { name: '文档草稿实时预览' });
    expect(preview).toHaveTextContent('第一天');
    expect(preview).toHaveTextContent('去中环');
  });

  it('修订时只提示进度，不展示片段替换的原始参数', () => {
    render(<DocumentDraftCard draft={{ draftId: 's1:0', toolName: 'edit_document', title: '', content: '', documentId: '' }} />);

    expect(screen.getByText('未命名文档')).toBeInTheDocument();
    expect(screen.getByText('正在修订文档')).toBeInTheDocument();
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
    expect(getDocumentMock).not.toHaveBeenCalled();
  });

  it('修订草稿拿到完整文档 id 后显示原文档标题', async () => {
    getDocumentMock.mockResolvedValueOnce({ id: DOC_ID, title: '入职第一周清单' });
    render(<DocumentDraftCard draft={{ draftId: 's1:0', toolName: 'edit_document', title: '', content: '', documentId: DOC_ID }} />);

    expect(await screen.findByText('入职第一周清单')).toBeInTheDocument();
    expect(getDocumentMock).toHaveBeenCalledWith(DOC_ID, expect.any(AbortSignal));
  });

  it('文档 id 片段尚未拼完整时不发请求', () => {
    getDocumentMock.mockClear();
    render(<DocumentDraftCard draft={{ draftId: 's1:0', toolName: 'edit_document', title: '', content: '', documentId: '3f2b8c1e-5d4a' }} />);

    expect(getDocumentMock).not.toHaveBeenCalled();
  });
});
