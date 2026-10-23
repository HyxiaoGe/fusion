'use client';

import type { ContextToolVisibility, ToolObservation } from "@/types/trajectory";

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import AdminSafeMarkdown from '@/components/admin/AdminSafeMarkdown';
import { describeToolArgument } from '@/lib/agent/toolRegistry';
import { useTrajectoryLlmNodeDetail } from '@/hooks/useTrajectoryLlmNodeDetail';
import { useTrajectorySystemPromptNodeDetail } from '@/hooks/useTrajectorySystemPromptNodeDetail';
import { useTrajectoryToolNodeDetail } from '@/hooks/useTrajectoryToolNodeDetail';
import {
  buildTrajectoryNodeDetailModel,
  systemPromptSectionLabel,
  type TrajectoryNodeDetailModel,
} from '@/lib/trajectory/trajectoryNodeDetailModel';
import { normalizeOutputProvenance } from '@/lib/trajectory/normalizeTrajectoryEvent';
import type { TrajectoryCell } from '@/lib/trajectory/TrajectoryCellProjection';
import {
  formatTrajectoryDuration,
  formatTrajectoryStatus,
  getTrajectoryCellPresentation,
  trajectoryPlanItems,
  trajectoryToolLabel,
} from '@/lib/trajectory/trajectoryCellPresentation';
import { extractTextFromBlocks, type ContentBlock } from '@/types/conversation';
import type {
  LlmOutputProvenance,
  TrajectoryNodeDetailResponse,
  TrajectorySpan,
} from '@/types/trajectory';
import { cn } from '@/lib/utils';

interface TrajectoryNodeDetailPanelProps {
  conversationId: string | null;
  cell: TrajectoryCell | null;
  span: TrajectorySpan | null;
  relatedCells?: readonly TrajectoryCell[];
}

type DetailSection =
  | 'summary'
  | 'preview'
  | 'raw'
  | 'source'
  | 'payload'
  | 'result'
  | 'prompt'
  | 'timing';
type RemoteDetailSectionName = 'payload' | 'result' | 'preview' | 'raw' | 'prompt';
type RemoteDetailKind = 'tool' | 'llm' | 'system_prompt';
type DetailRequestStatus = 'idle' | 'loading' | 'ready' | 'failed';

interface PendingWindow {
  deadline: number;
  requestCount: number;
}

const TOOL_SECTIONS: readonly DetailSection[] = ['summary', 'payload', 'result', 'timing'];
const LLM_SECTIONS: readonly DetailSection[] = ['summary', 'preview', 'raw'];
const MESSAGE_SECTIONS: readonly DetailSection[] = ['summary', 'preview', 'raw', 'source'];
const SYSTEM_PROMPT_SECTIONS: readonly DetailSection[] = ['prompt', 'summary', 'timing'];
const LOCAL_SECTIONS: readonly DetailSection[] = ['summary', 'timing'];
const SUMMARY_ONLY_SECTIONS: readonly DetailSection[] = ['summary'];
const SECTION_LABELS: Record<Exclude<DetailSection, 'prompt'>, string> = {
  summary: '摘要',
  preview: '预览',
  raw: '原始',
  source: '来源',
  payload: '载荷',
  result: '结果',
  timing: '计时',
};
const PENDING_RETRY_INTERVAL_MS = 1_000;
const PENDING_RETRY_DEADLINE_MS = 7_000;
const PENDING_MAX_REQUESTS = 7;
const EMPTY_RELATED_CELLS: readonly TrajectoryCell[] = [];
const SUMMARY_PAYLOAD_FIELDS = 6;

export function TrajectoryNodeDetailPanel({
  conversationId,
  cell,
  span,
  relatedCells = EMPTY_RELATED_CELLS,
}: TrajectoryNodeDetailPanelProps) {
  return (
    <aside
      aria-label="轨迹节点详情"
      className="min-h-full bg-background p-3"
    >
      {cell ? (
        <TrajectoryNodeDetailContent
          key={`${conversationId ?? 'no-conversation'}:${cell.runId ?? 'no-run'}:${cell.key}`}
          conversationId={conversationId}
          cell={cell}
          span={span}
          relatedCells={relatedCells}
        />
      ) : (
        <div className="flex min-h-40 items-center justify-center text-sm text-muted-foreground">
          选择一条记录查看详情
        </div>
      )}
    </aside>
  );
}

function TrajectoryNodeDetailContent({
  conversationId,
  cell,
  span,
  relatedCells,
}: {
  conversationId: string | null;
  cell: TrajectoryCell;
  span: TrajectorySpan | null;
  relatedCells: readonly TrajectoryCell[];
}) {
  const { t } = useTranslation();
  const isUser = cell.type === 'user';
  const isMessage = cell.type === 'message';
  const isTool = cell.type === 'tool';
  const isLlm = cell.type === 'assistant_request';
  const isSystemPrompt = cell.type === 'context' && cell.eventType === 'system_prompt_prepared';
  const sections = isSystemPrompt
    ? SYSTEM_PROMPT_SECTIONS
    : isUser || isMessage
      ? MESSAGE_SECTIONS
      : isTool
        ? TOOL_SECTIONS
        : isLlm
          ? (cell.detailAvailable ? LLM_SECTIONS : SUMMARY_ONLY_SECTIONS)
          : LOCAL_SECTIONS;
  const model = useMemo(
    () => buildTrajectoryNodeDetailModel(cell, span, relatedCells, t),
    [cell, relatedCells, span, t],
  );
  const tabsId = useId();
  const tabRefs = useRef<Partial<Record<DetailSection, HTMLButtonElement | null>>>({});
  const startsWithPrompt = isSystemPrompt;
  // 摘要页要直接给出调用、结果和本轮输出的预览，打开节点即读取详情。
  const startsWithDetail = startsWithPrompt || needsRemoteDetail(cell, 'summary');
  const [activeSection, setActiveSection] = useState<DetailSection>(startsWithPrompt ? 'prompt' : 'summary');
  const [detailRequested, setDetailRequested] = useState(startsWithDetail);
  const [pendingWindow, setPendingWindow] = useState<PendingWindow | null>(() => (
    startsWithDetail
      ? { deadline: performance.now() + PENDING_RETRY_DEADLINE_MS, requestCount: 1 }
      : null
  ));
  const toolDetail = useTrajectoryToolNodeDetail(
    isTool
      ? {
        conversationId,
        runId: cell.runId,
        nodeType: 'tool',
        toolCallId: cell.toolCallId,
      }
      : null,
    isTool && detailRequested,
  );
  const llmDetail = useTrajectoryLlmNodeDetail(
    isLlm
      ? {
        conversationId,
        runId: cell.runId,
        llmRoundId: cell.llmRoundId,
      }
      : null,
    isLlm && cell.detailAvailable && detailRequested,
  );
  const systemPromptDetail = useTrajectorySystemPromptNodeDetail(
    isSystemPrompt ? { conversationId, runId: cell.runId } : null,
    isSystemPrompt && detailRequested,
  );
  const {
    status: requestStatus,
    response,
    error,
    retry,
  } = isSystemPrompt
    ? systemPromptDetail
    : isLlm
      ? llmDetail
      : toolDetail;
  const remoteDetailKind: RemoteDetailKind = isSystemPrompt
    ? 'system_prompt'
    : isLlm
      ? 'llm'
      : 'tool';
  const isRemoteSection = needsRemoteDetail(cell, activeSection);
  const pendingStopped = isRemoteSection
    && requestStatus === 'ready'
    && response?.status === 'pending'
    && (
      pendingWindow === null
      || pendingWindow.requestCount >= PENDING_MAX_REQUESTS
    );

  useEffect(() => {
    if (
      !isRemoteSection
      || requestStatus !== 'ready'
      || response?.status !== 'pending'
      || !pendingWindow
      || pendingWindow.requestCount >= PENDING_MAX_REQUESTS
    ) return;

    const remaining = pendingWindow.deadline - performance.now();
    if (remaining <= 0) {
      const expiredTimer = window.setTimeout(() => setPendingWindow(null), 0);
      return () => window.clearTimeout(expiredTimer);
    }
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'hidden' || performance.now() >= pendingWindow.deadline) {
        setPendingWindow(null);
        return;
      }
      setPendingWindow(current => current
        ? { ...current, requestCount: current.requestCount + 1 }
        : null);
      retry();
    }, Math.min(PENDING_RETRY_INTERVAL_MS, remaining));

    return () => window.clearTimeout(timer);
  }, [isRemoteSection, pendingWindow, requestStatus, response?.status, retry]);

  useEffect(() => {
    if (!isRemoteSection || response?.status !== 'pending') return;
    const stopWhenHidden = () => {
      if (document.visibilityState === 'hidden') setPendingWindow(null);
    };
    document.addEventListener('visibilitychange', stopWhenHidden);
    return () => document.removeEventListener('visibilitychange', stopWhenHidden);
  }, [isRemoteSection, response?.status]);

  function beginPendingWindow(requestCount: number) {
    setPendingWindow({
      deadline: performance.now() + PENDING_RETRY_DEADLINE_MS,
      requestCount,
    });
  }

  function selectSection(section: DetailSection, moveFocus = false) {
    setActiveSection(section);
    if (needsRemoteDetail(cell, section)) {
      if (!detailRequested) {
        beginPendingWindow(1);
        setDetailRequested(true);
      } else if (response?.status === 'pending' && !pendingWindow) {
        beginPendingWindow(0);
      }
    } else {
      setPendingWindow(null);
    }
    if (moveFocus) tabRefs.current[section]?.focus();
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    const currentIndex = sections.indexOf(activeSection);
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (currentIndex + 1) % sections.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (currentIndex - 1 + sections.length) % sections.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = sections.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    selectSection(sections[nextIndex], true);
  }

  function retryDetail() {
    beginPendingWindow(1);
    retry();
  }

  return (
    <div className="min-w-0">
      <div className="mb-4 min-w-0">
        <h2 className="truncate text-base font-semibold text-foreground">
          {isSystemPrompt
            ? t('trajectory.systemPrompt.title')
            : model.title}
        </h2>
      </div>

      <div
        role="tablist"
        aria-label="节点详情栏目"
        className="flex gap-1 overflow-x-auto border-b border-border/60"
      >
        {sections.map(section => (
          <button
            key={section}
            ref={node => { tabRefs.current[section] = node; }}
            id={`${tabsId}-${section}-tab`}
            type="button"
            role="tab"
            aria-selected={activeSection === section}
            aria-controls={`${tabsId}-${section}-panel`}
            tabIndex={activeSection === section ? 0 : -1}
            onClick={() => selectSection(section)}
            onKeyDown={handleTabKeyDown}
            className={cn(
              'cursor-pointer border-b-2 px-3 py-2 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring',
              activeSection === section
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {section === 'prompt'
              ? t('trajectory.systemPrompt.body')
              : SECTION_LABELS[section]}
          </button>
        ))}
      </div>

      <section
        id={`${tabsId}-${activeSection}-panel`}
        role="tabpanel"
        aria-labelledby={`${tabsId}-${activeSection}-tab`}
        className="min-w-0 pt-4"
      >
        {activeSection === 'summary' && (
          <SummarySection
            cell={cell}
            model={model}
            relatedCells={relatedCells}
            detail={{ requestStatus, response }}
            onSelectSection={selectSection}
          />
        )}
        {activeSection === 'preview' && isLlm && (
          <OutputProvenanceSection provenance={
            cell.outputProvenance ?? normalizeOutputProvenance(
              response?.detail && 'llm_round_id' in response.detail
                ? response.detail.output_provenance : null,
            )
          } />
        )}
        {activeSection === 'preview' && (cell.type === 'user' || cell.type === 'message') && (
          <MessagePreviewSection cell={cell} />
        )}
        {activeSection === 'raw' && (cell.type === 'user' || cell.type === 'message') && (
          <MessageRawSection cell={cell} />
        )}
        {activeSection === 'source' && (cell.type === 'user' || cell.type === 'message') && (
          <MessageSourceSection cell={cell} model={model} />
        )}
        {activeSection === 'timing' && <TimingSection model={model} />}
        {isRemoteSection && activeSection !== 'summary' && (
          <RemoteDetailSection
            section={activeSection as RemoteDetailSectionName}
            requestStatus={requestStatus}
            response={response}
            error={error}
            pendingStopped={pendingStopped}
            onRetry={retryDetail}
            detailKind={remoteDetailKind}
            relatedCells={relatedCells}
          />
        )}
      </section>

      {model.diagnostics.length > 0 && (
        <details
          role="group"
          aria-label="诊断信息"
          className="mt-4 border-b border-border/45 pb-2"
        >
          <summary className="cursor-pointer py-1 text-sm font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            诊断信息
          </summary>
          <dl className="mt-1 divide-y divide-border/35 text-xs">
            {model.diagnostics.map(item => (
              <div key={`${item.label}:${item.value}`} className={SUMMARY_FIELD_GRID}>
                <dt className="text-muted-foreground">{item.label}</dt>
                <dd className="min-w-0 break-all font-mono text-foreground">{item.value}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}

function needsRemoteDetail(cell: TrajectoryCell, section: DetailSection): boolean {
  if (cell.type === 'context' && cell.eventType === 'system_prompt_prepared') return section === 'prompt';
  if (cell.type === 'tool') return section === 'summary' || section === 'payload' || section === 'result';
  if (cell.type === 'assistant_request') {
    return cell.detailAvailable && (section === 'summary' || section === 'preview' || section === 'raw');
  }
  return false;
}

function MessagePreviewSection({
  cell,
}: {
  cell: Extract<TrajectoryCell, { type: 'user' | 'message' }>;
}) {
  const text = extractTextFromBlocks(cell.message.content);
  const files = cell.message.content.filter(
    (block): block is Extract<ContentBlock, { type: 'file' }> => block.type === 'file',
  );

  return (
    <div className="space-y-4">
      {text.trim() ? (
        <AdminSafeMarkdown content={text} className="text-sm" />
      ) : (
        <p className="text-sm text-muted-foreground">无文字内容</p>
      )}
      {files.length > 0 && (
        <div
          className="space-y-2"
          aria-label={cell.type === 'user' ? '用户附件' : '回答附件'}
        >
          {files.map(file => (
            <div
              key={file.id}
              className="rounded-md border border-border/60 bg-muted/15 px-3 py-2"
            >
              <p className="break-words text-sm font-medium text-foreground">{file.filename}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{file.mime_type}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MessageRawSection({
  cell,
}: {
  cell: Extract<TrajectoryCell, { type: 'user' | 'message' }>;
}) {
  const blocks = cell.message.content.filter(block => (
    block.type !== 'thinking'
    && (
      cell.type !== 'message'
      || !FINAL_MESSAGE_EVIDENCE_BLOCK_TYPES.has(block.type)
    )
  ));
  if (blocks.length === 0) {
    return <p className="text-sm text-muted-foreground">没有可展示的原始内容块</p>;
  }

  return (
    <div className="space-y-4">
      {blocks.map((block, index) => (
        <section key={block.id} className="min-w-0 space-y-1.5">
          <h3 className="text-xs font-medium text-muted-foreground">
            Block #{index + 1} · {block.type}
          </h3>
          <pre className="max-h-96 whitespace-pre-wrap break-words overflow-auto rounded-md border border-border/60 bg-muted/20 p-3 text-xs text-foreground">
            <code>{formatMessageRawBlock(block)}</code>
          </pre>
        </section>
      ))}
    </div>
  );
}

const FINAL_MESSAGE_EVIDENCE_BLOCK_TYPES = new Set<ContentBlock['type']>([
  'search',
  'url_read',
  'knowledge_evidence',
]);

function MessageSourceSection({
  cell,
  model,
}: {
  cell: Extract<TrajectoryCell, { type: 'user' | 'message' }>;
  model: TrajectoryNodeDetailModel;
}) {
  const source = {
    kind: cell.type === 'user' ? 'user' : 'assistant',
    source: 'messages',
    messageId: cell.message.id,
    sequence: cell.message.sequence ?? null,
    timestamp: model.startedAt,
    ...(cell.type === 'message' ? { modelId: cell.message.model_id ?? null } : {}),
  };

  return (
    <pre className="max-h-96 whitespace-pre-wrap break-words overflow-auto rounded-md border border-border/60 bg-muted/20 p-3 text-xs text-foreground">
      <code>{JSON.stringify(source, null, 2)}</code>
    </pre>
  );
}

function formatMessageRawBlock(block: ContentBlock): string {
  if (block.type === 'text') return block.text;
  return JSON.stringify(sanitizeRawValue(block), null, 2);
}

const PRIVATE_RAW_KEYS = new Set(['id', 'file_id', 'thumbnail_url', 'tool_call_log_id']);

function sanitizeRawValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizeRawValue);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !PRIVATE_RAW_KEYS.has(key))
      .map(([key, entry]) => [key, sanitizeRawValue(entry)]),
  );
}

interface SummaryDetailState {
  requestStatus: DetailRequestStatus;
  response: TrajectoryNodeDetailResponse | null;
}

function SummarySection({
  cell,
  model,
  relatedCells,
  detail,
  onSelectSection,
}: {
  cell: TrajectoryCell;
  model: TrajectoryNodeDetailModel;
  relatedCells: readonly TrajectoryCell[];
  detail: SummaryDetailState;
  onSelectSection: (section: DetailSection) => void;
}) {
  const sourceFields = model.summaryFields.filter(field => field.label === '来源');
  const otherFields = model.summaryFields.filter(field => field.label !== '来源' && field.label !== '状态');
  const fields = [
    ...sourceFields,
    { label: '状态', value: model.status },
    ...otherFields,
    ...(cell.type === 'tool' && model.duration ? [{ label: '耗时', value: model.duration }] : []),
    ...(model.attemptCount !== null && model.attemptMode
      ? [{
        label: model.attemptMode === 'count' ? '尝试次数' : '尝试',
        value: model.attemptMode === 'count'
          ? `${model.attemptCount} 次`
          : `第 ${model.attemptCount} 次`,
      }]
      : []),
    ...(!['user', 'message', 'assistant_request', 'tool', 'plan'].includes(cell.type)
      ? [{ label: '概览', value: model.summary }]
      : []),
  ];

  return (
    <div className="space-y-4">
      <dl className="divide-y divide-border/45 border-y border-border/45">
        {fields.map(field => (
          <SummaryFieldRow key={field.label} label={field.label} value={field.value} />
        ))}
      </dl>
      {model.errorSummary && (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-foreground">
          <p className="mb-1 text-xs font-medium text-danger">异常摘要</p>
          <p>{model.errorSummary}</p>
        </div>
      )}
      {cell.type === 'tool' && (
        <SummaryBlock title="调用" actionLabel="查看完整载荷" onOpen={() => onSelectSection('payload')}>
          <ToolCallPreview toolName={cell.toolName} detail={detail} />
        </SummaryBlock>
      )}
      {cell.type === 'assistant_request' && (
        <SummaryBlock
          title="预览"
          actionLabel="查看完整预览"
          onOpen={cell.detailAvailable ? () => onSelectSection('preview') : undefined}
        >
          <LlmRoundPreview cell={cell} relatedCells={relatedCells} detail={detail} />
        </SummaryBlock>
      )}
      {cell.type === 'assistant_request' && <RequestTimingSummary model={model} />}
      {cell.type === 'plan' && (
        <SummaryBlock title="步骤" actionLabel="步骤">
          <PlanStepList cell={cell} />
        </SummaryBlock>
      )}
    </div>
  );
}

function PlanStepList({ cell }: { cell: Extract<TrajectoryCell, { type: 'plan' }> }) {
  const items = trajectoryPlanItems(cell.payload);
  if (items.length === 0) return <SummaryNotice text="计划没有步骤" />;
  return (
    <div className="min-w-0 space-y-2">
      {cell.superseded && <SummaryNotice text="这是模型当时写下的计划，之后又更新过；最新进度看最后一版计划。" />}
      <ol aria-label="计划步骤" className="space-y-1.5 text-sm">
        {items.map((item, index) => (
          <li key={item.id} className="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-baseline gap-2">
            <span className="text-xs tabular-nums text-muted-foreground">{index + 1}.</span>
            <span className="min-w-0 break-words text-foreground">
              {item.title}
              {item.summary && <span className="block text-xs text-muted-foreground">{item.summary}</span>}
            </span>
            {!cell.superseded && (
              <span className={cn('text-xs', PLAN_STATUS_TONE[item.status])}>{item.statusLabel}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

const PLAN_STATUS_TONE: Record<string, string> = {
  completed: 'text-success',
  running: 'text-info',
  failed: 'text-danger',
  blocked: 'text-warn',
  skipped: 'text-muted-foreground',
  pending: 'text-muted-foreground',
};

function SummaryBlock({
  title,
  actionLabel,
  onOpen,
  children,
}: {
  title: string;
  actionLabel: string;
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="min-w-0 space-y-2 border-b border-border/45 pb-3">
      {onOpen ? (
        <button
          type="button"
          aria-label={actionLabel}
          onClick={onOpen}
          className="group flex cursor-pointer items-center gap-0.5 text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {title}
          <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 text-muted-foreground group-hover:text-foreground" />
        </button>
      ) : (
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
      )}
      {children}
    </section>
  );
}

/** 详情尚未可用时的一句说明；重试和完整原因留在对应页签。 */
function summaryDetailNotice(detail: SummaryDetailState): string | null {
  if (detail.requestStatus === 'idle' || detail.requestStatus === 'loading') return '正在加载';
  if (detail.requestStatus === 'failed') return '加载失败，可在对应页签重试';
  const response = detail.response;
  if (!response) return '详情不可用';
  if (response.status === 'pending') return '详情仍在落账';
  if (response.status === 'not_recorded') return '该运行生成时尚未记录详情';
  if (response.status === 'degraded') return '详情记录不完整，可在对应页签查看说明';
  return null;
}

function SummaryNotice({ text }: { text: string }) {
  return <p className="text-xs text-muted-foreground">{text}</p>;
}

function ToolCallPreview({ toolName, detail }: { toolName: string | null; detail: SummaryDetailState }) {
  const notice = summaryDetailNotice(detail);
  if (notice) return <SummaryNotice text={notice} />;
  const payload = detail.response ? remoteSectionValue(detail.response, 'payload') : null;
  if (!payload || Object.keys(payload).length === 0) return <SummaryNotice text="没有参数" />;
  const entries = Object.entries(payload);
  return (
    <div className="min-w-0 space-y-1.5">
      <dl className="space-y-1 text-sm">
        {entries.slice(0, SUMMARY_PAYLOAD_FIELDS).map(([key, value]) => {
          const argument = describeToolArgument(toolName ?? '', key, formatPayloadValue(value));
          return (
            <div key={key} className="grid grid-cols-[minmax(4rem,max-content)_minmax(0,1fr)] gap-3">
              <dt className="text-muted-foreground">{argument.label}</dt>
              <dd className="line-clamp-3 min-w-0 break-words text-foreground">{argument.value}</dd>
            </div>
          );
        })}
      </dl>
      {entries.length > SUMMARY_PAYLOAD_FIELDS && (
        <SummaryNotice text={`另有 ${entries.length - SUMMARY_PAYLOAD_FIELDS} 项参数`} />
      )}
    </div>
  );
}

interface ResultLink {
  title: string;
  url: string;
  excerpt: string | null;
}

/** 工具返回里第一组带标题和网址的条目（搜索来源、图片等）；没有时为 null。 */
function resultLinks(result: Record<string, unknown>): ResultLink[] | null {
  for (const value of Object.values(result)) {
    if (!Array.isArray(value) || value.length === 0) continue;
    const links = value.flatMap(item => {
      if (!item || typeof item !== 'object') return [];
      const link = toResultLink(item as Record<string, unknown>);
      return link ? [link] : [];
    });
    if (links.length === value.length) return links;
  }
  return null;
}

function toResultLink(record: Record<string, unknown>): ResultLink | null {
  const title = typeof record.title === 'string' ? record.title.trim() : '';
  const url = [record.url, record.page_url].find(
    (value): value is string => typeof value === 'string' && /^https?:\/\//.test(value),
  );
  if (!title || !url) return null;
  const excerpt = [record.content, record.description].find(
    (value): value is string => typeof value === 'string' && Boolean(value.trim()),
  );
  return { title, url, excerpt: excerpt?.trim() ?? null };
}

function ResultLinkList({ links }: { links: readonly ResultLink[] }) {
  return (
    <ol className="min-w-0 space-y-1.5 text-sm">
      {links.map((link, index) => (
        <li key={`${index}:${link.url}`} className="min-w-0">
          <a
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="break-words text-foreground underline-offset-2 hover:underline"
          >
            {link.title}
          </a>
          <span className="block truncate text-xs text-muted-foreground">{hostLabel(link.url)}</span>
          {link.excerpt && (
            <p className="mt-0.5 line-clamp-3 break-words text-xs text-muted-foreground">{link.excerpt}</p>
          )}
        </li>
      ))}
    </ol>
  );
}

function hostLabel(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function LlmRoundPreview({
  cell,
  relatedCells,
  detail,
}: {
  cell: Extract<TrajectoryCell, { type: 'assistant_request' }>;
  relatedCells: readonly TrajectoryCell[];
  detail: SummaryDetailState;
}) {
  const notice = cell.detailAvailable ? summaryDetailNotice(detail) : null;
  const llmDetail = detail.response?.detail && 'llm_round_id' in detail.response.detail
    ? detail.response.detail
    : null;
  const sections = detail.response?.available_sections ?? [];
  const reasoning = sections.includes('thinking') ? llmDetail?.reasoning_text?.trim() : '';
  const output = sections.includes('output') ? llmDetail?.output_text?.trim() : '';
  const tools = roundToolCells(cell, relatedCells);
  const plans = relatedCells.filter((related): related is Extract<TrajectoryCell, { type: 'plan' }> => (
    related.type === 'plan' && related.llmRoundKey === cell.key
  ));
  const empty = !notice && !reasoning && !output && tools.length === 0 && plans.length === 0;

  return (
    <div className="min-w-0 space-y-2.5">
      {notice && <SummaryNotice text={notice} />}
      {reasoning && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
            思考
          </summary>
          <div className="mt-1.5 max-h-60 overflow-auto border-l-2 border-border/60 pl-3 text-muted-foreground">
            <AdminSafeMarkdown content={reasoning} className="text-sm" />
          </div>
        </details>
      )}
      {output && (
        <div className="max-h-60 overflow-auto">
          <AdminSafeMarkdown content={output} className="text-sm" />
        </div>
      )}
      {plans.map(plan => (
        <p key={plan.key} className="flex min-w-0 items-baseline gap-2 text-xs">
          <span className="shrink-0 font-medium text-foreground">更新计划</span>
          <span className="truncate text-muted-foreground">{getTrajectoryCellPresentation(plan).summary}</span>
        </p>
      ))}
      {tools.length > 0 && (
        <ul aria-label="本轮发起的工具调用" className="space-y-1 text-xs">
          {tools.map(tool => (
            <li key={tool.toolCallId} className="flex min-w-0 items-baseline gap-2">
              <span className="shrink-0 font-medium text-foreground">{trajectoryToolLabel(tool.toolName, tool.toolLabel)}</span>
              <span className="truncate text-muted-foreground">
                {[formatTrajectoryStatus(tool.status), formatTrajectoryDuration(toolDurationMs(tool))]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </li>
          ))}
        </ul>
      )}
      {empty && <SummaryNotice text="本轮没有可见输出" />}
    </div>
  );
}

/** 只有以工具调用结束的一轮才认领同一步骤里的工具，失败后重试的那轮不重复列出。 */
function roundToolCells(
  cell: Extract<TrajectoryCell, { type: 'assistant_request' }>,
  relatedCells: readonly TrajectoryCell[],
): Extract<TrajectoryCell, { type: 'tool' }>[] {
  const roundEvent = (eventType: string) => cell.events.find(event => (
    event.eventType === eventType && event.payload.llm_round_id === cell.llmRoundId
  ));
  if (roundEvent('llm_round_completed')?.payload.finish_reason !== 'tool_calls') return [];
  const stepId = roundEvent('llm_round_started')?.stepId;
  if (!stepId) return [];
  return relatedCells.filter((related): related is Extract<TrajectoryCell, { type: 'tool' }> => (
    related.type === 'tool' && related.runId === cell.runId && related.stepId === stepId
  ));
}

function toolDurationMs(cell: Extract<TrajectoryCell, { type: 'tool' }>): number | null {
  const duration = cell.events.findLast(event => event.eventType === 'tool_call_completed')?.payload.duration_ms;
  return typeof duration === 'number' ? duration : null;
}

function formatPayloadValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value) ?? '';
}

const SUMMARY_FIELD_GRID = 'grid grid-cols-[5.5rem_minmax(0,1fr)] gap-3 py-2';

function SummaryFieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div className={cn(SUMMARY_FIELD_GRID, 'text-sm')}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-foreground">{value}</dd>
    </div>
  );
}

function RequestTimingSummary({ model }: { model: TrajectoryNodeDetailModel }) {
  const fields = [
    ['总耗时', model.duration],
    ['首次输出', model.ttft],
    ['开始时间', model.startedAt],
    ['结束时间', model.endedAt],
  ].filter((entry): entry is [string, string] => entry[1] !== null);

  if (fields.length === 0) return null;
  return (
    <details open className="border-b border-border/45 pb-2">
      <summary className="cursor-pointer py-1 text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
        请求计时
      </summary>
      <dl className="mt-1 divide-y divide-border/35">
        {fields.map(([label, value]) => (
          <SummaryFieldRow key={label} label={label} value={value} />
        ))}
      </dl>
    </details>
  );
}

function TimingSection({ model }: { model: TrajectoryNodeDetailModel }) {
  const fields = [
    ['耗时', model.duration],
    ['首次输出', model.ttft],
    ['开始时间', model.startedAt],
    ['结束时间', model.endedAt],
  ].filter((entry): entry is [string, string] => entry[1] !== null);

  if (fields.length === 0) {
    return <p className="text-sm text-muted-foreground">暂无可用时间信息</p>;
  }
  return (
    <dl className="grid gap-2 sm:grid-cols-2">
      {fields.map(([label, value]) => <DetailField key={label} label={label} value={value} />)}
    </dl>
  );
}

function DetailField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-2.5">
      <dt className="text-[11px] font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words text-sm text-foreground">{value}</dd>
    </div>
  );
}

function RemoteDetailSection({
  section,
  requestStatus,
  response,
  error,
  pendingStopped,
  onRetry,
  detailKind,
  relatedCells,
}: {
  section: RemoteDetailSectionName;
  requestStatus: DetailRequestStatus;
  response: TrajectoryNodeDetailResponse | null;
  error: string | null;
  pendingStopped: boolean;
  onRetry: () => void;
  detailKind: RemoteDetailKind;
  relatedCells: readonly TrajectoryCell[];
}) {
  const { t } = useTranslation();
  const isSystemPrompt = detailKind === 'system_prompt';
  if (requestStatus === 'idle' || requestStatus === 'loading') {
    return <p role="status" className="text-sm text-muted-foreground">正在加载详情</p>;
  }
  if (requestStatus === 'failed') {
    return (
      <div className="space-y-3 text-sm text-danger">
        <p role="alert">{error ?? '加载节点详情失败，请稍后重试'}</p>
        <RetryButton label="重试" onClick={onRetry} />
      </div>
    );
  }
  if (!response) return <p role="alert" className="text-sm text-danger">节点详情不可用</p>;

  if (response.status === 'pending') {
    return (
      <div className="space-y-2 text-sm text-muted-foreground">
        <div role="status">
          <p>{isSystemPrompt
            ? t('trajectory.systemPrompt.pending')
            : '详情仍在落账'}</p>
          {pendingStopped && <p>自动检查已停止</p>}
        </div>
        <RetryButton label="重新检查" onClick={onRetry} />
      </div>
    );
  }
  if (response.status === 'not_recorded') {
    return (
      <p className="text-sm text-muted-foreground">
        {isSystemPrompt
          ? t('trajectory.systemPrompt.notRecorded')
          : '该运行生成时尚未记录 Payload/Result'}
      </p>
    );
  }
  if (response.status === 'degraded') {
    if (isSystemPrompt) {
      const messageKey = response.reason === 'system_prompt_assembly_failed'
        ? 'assemblyFailed'
        : response.reason === 'system_prompt_detail_invalid'
          ? 'bodyInvalid'
          : 'bodyMissing';
      return <p className="text-sm text-warn">{t(`trajectory.systemPrompt.${messageKey}`)}</p>;
    }
    if (response.reason === 'tool_detail_invalid') {
      return <p className="text-sm text-warn">{t('trajectory.toolDetail.invalid')}</p>;
    }
    return (
      <p className="text-sm text-warn">
        {response.node_type === 'llm'
          ? '运行已结束，但模型正文未能完成记录'
          : '运行已结束，但工具详情未能精确关联'}
      </p>
    );
  }

  if (isSystemPrompt) return <SystemPromptAvailableDetailSection response={response} />;

  const redactedFields = response.redacted_fields ?? [];
  const truncatedFields = response.truncated_fields ?? [];
  if (response.node_type === 'llm') {
    return (
      <LlmAvailableDetailSection
        section={section}
        response={response}
        redactedFields={redactedFields}
        truncatedFields={truncatedFields}
        relatedCells={relatedCells}
      />
    );
  }

  const toolSection = section === 'payload' || section === 'result' ? section : null;
  const value = toolSection && response.available_sections.includes(toolSection)
    ? remoteSectionValue(response, toolSection)
    : null;
  const links = toolSection === 'result' && value ? resultLinks(value) : null;
  return (
    <div className="space-y-3">
      {response.reason === 'tool_detail_legacy_summary' && (
        <p className="text-sm text-muted-foreground">{t('trajectory.toolDetail.legacySummary')}</p>
      )}
      {toolSection === 'result' && <h3 className="text-sm font-semibold">{t('trajectory.observation.toolReturn')}</h3>}
      {links && <ResultLinkList links={links} />}
      {value === null ? (
        <p className="text-sm text-muted-foreground">该部分未提供</p>
      ) : links ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            原始返回
          </summary>
          <JsonBlock value={value} />
        </details>
      ) : (
        <JsonBlock value={value} />
      )}
      <DetailWarnings redactedFields={redactedFields} truncatedFields={truncatedFields} />
      {toolSection === 'result' && <ToolObservationSection observation={
        response.detail && 'tool_call_id' in response.detail ? response.detail.observation : undefined
      } />}
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="mt-1.5 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-muted/20 p-3 text-xs text-foreground">
      <code>{JSON.stringify(value, null, 2)}</code>
    </pre>
  );
}

function ToolObservationSection({ observation }: { observation?: ToolObservation }) {
  const { t } = useTranslation();
  return (
    <details className="border-t border-border/60 pt-3">
      <summary className="cursor-pointer text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {t('trajectory.observation.title')}
      </summary>
      <div className="mt-2 space-y-3">
        <p className="text-xs text-muted-foreground">{t('trajectory.observation.scope')}</p>
        {observation?.status === 'available' && typeof observation.text === 'string' ? <>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-muted/20 p-3 text-xs">{observation.text}</pre>
          <p className="text-xs text-muted-foreground">{t('trajectory.observation.originalChars', { count: observation.original_chars ?? undefined })}</p>
          <DetailWarnings redactedFields={observation.redacted_fields ?? []} truncatedFields={observation.truncated_fields ?? []} />
        </> : <p className="text-sm text-muted-foreground">{t(`trajectory.observation.${observation?.status === 'capture_failed' ? 'failed' : 'notRecorded'}`)}</p>}
      </div>
    </details>
  );
}

/** 本轮提交给模型的工具结果，按工具名计数；还没有工具结果的轮次不显示。 */
function ContextVisibilitySection({
  visibility,
  relatedCells,
}: {
  visibility?: ContextToolVisibility | null;
  relatedCells: readonly TrajectoryCell[];
}) {
  const { t } = useTranslation();
  if (!visibility || visibility.before_count === 0) return null;
  const toolLabels = new Map(relatedCells.flatMap(related => (
    related.type === 'tool'
      ? [[related.toolCallId, trajectoryToolLabel(related.toolName, related.toolLabel)] as const]
      : []
  )));
  const describe = (ids: readonly string[]) => {
    const counts = new Map<string, number>();
    for (const id of ids) {
      const label = toolLabels.get(id) ?? t('trajectory.contextVisibility.unknownTool');
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return [...counts].map(([label, count]) => (count > 1 ? `${label}×${count}` : label)).join('、');
  };
  return (
    <section className="space-y-1.5 text-sm">
      <h3 className="font-semibold text-foreground">{t('trajectory.contextVisibility.title')}</h3>
      <p className="break-words text-foreground">
        {t('trajectory.contextVisibility.visible', { count: visibility.visible_count })}
        {visibility.visible_tool_call_ids.length > 0 && `：${describe(visibility.visible_tool_call_ids)}`}
      </p>
      {visibility.removed_count > 0 && (
        <p className="break-words text-muted-foreground">
          {t('trajectory.contextVisibility.removed', { count: visibility.removed_count })}
          {visibility.removed_tool_call_ids.length > 0 && `：${describe(visibility.removed_tool_call_ids)}`}
        </p>
      )}
    </section>
  );
}

function SystemPromptAvailableDetailSection({ response }: { response: TrajectoryNodeDetailResponse }) {
  const { t } = useTranslation();
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle');
  const detail = response.node_type === 'system_prompt'
    && response.available_sections.includes('prompt')
    && response.detail && 'sections' in response.detail
    ? response.detail
    : null;

  if (
    !detail
    || !Array.isArray(detail.sections)
    || detail.sections.length === 0
    || !detail.sections.every(section => (
      section && typeof section.section_id === 'string' && typeof section.content === 'string'
    ))
  ) {
    return <p className="text-sm text-warn">{t('trajectory.systemPrompt.bodyInvalid')}</p>;
  }

  const fullPrompt = detail.sections.map(section => section.content).join('\n\n');

  async function copyFullPrompt() {
    try {
      await navigator.clipboard.writeText(fullPrompt);
      setCopyStatus('copied');
    } catch {
      setCopyStatus('failed');
    }
  }

  return (
    <div className="min-w-0 space-y-4">
      <p className="text-sm text-muted-foreground">
        {t('trajectory.systemPrompt.scopeNote')}
      </p>
      <button
        type="button"
        onClick={copyFullPrompt}
        className="cursor-pointer rounded-md border border-border px-3 py-1.5 text-sm text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
      >
        {t(`trajectory.systemPrompt.${copyStatus === 'copied' ? 'copiedFull' : 'copyFull'}`)}
      </button>
      {copyStatus === 'failed' && (
        <p role="alert" className="text-sm text-danger">{t('trajectory.systemPrompt.copyFailed')}</p>
      )}
      {detail.sections.map((section, index) => (
        <section key={`${index}:${section.section_id}`} className="min-w-0 space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">{systemPromptSectionLabel(section.section_id, t)}</h3>
          <pre className="whitespace-pre-wrap break-words rounded-md border border-border/60 bg-muted/20 p-3 text-xs text-foreground">
            {section.content}
          </pre>
        </section>
      ))}
    </div>
  );
}

/** 正文照常显示、转去调用工具或本轮没有正文是常态，不占位置；只说明被改写、撤回或没显示的情况。 */
const ROUTINE_PROVENANCE_REASONS = new Set(['streamed', 'tool_round', 'no_content']);

function OutputProvenanceSection({ provenance }: { provenance: LlmOutputProvenance | null }) {
  const { t } = useTranslation();
  if (!provenance || ROUTINE_PROVENANCE_REASONS.has(provenance.reason)) return null;
  const disposition = provenance.reason === 'tool_retracted' ? 'retracted' : provenance.disposition;
  return (
    <section className="mb-4 space-y-1 border-b border-border/45 pb-3 text-sm">
      <h3 className="font-semibold text-foreground">
        {t('trajectory.outputProvenance.title')} · {t(`trajectory.outputProvenance.${disposition}`)}
      </h3>
      <p className="text-muted-foreground">{t(`trajectory.outputProvenance.reasons.${provenance.reason}`)}</p>
    </section>
  );
}

function LlmAvailableDetailSection({
  section,
  response,
  redactedFields,
  truncatedFields,
  relatedCells,
}: {
  section: RemoteDetailSectionName;
  response: TrajectoryNodeDetailResponse;
  redactedFields: readonly string[];
  truncatedFields: readonly string[];
  relatedCells: readonly TrajectoryCell[];
}) {
  const detail = response.detail && 'llm_round_id' in response.detail
    ? response.detail
    : null;
  if (!detail || (section !== 'preview' && section !== 'raw')) {
    return <p className="text-sm text-muted-foreground">该部分未提供</p>;
  }

  const hasReasoning = response.available_sections.includes('thinking')
    && Boolean(detail.reasoning_text?.trim());
  const hasOutput = response.available_sections.includes('output')
    && Boolean(detail.output_text?.trim());

  return (
    <div className="space-y-4">
      {section === 'preview' ? (
        <div className="space-y-5">
          <ContextVisibilitySection visibility={detail.context_visibility} relatedCells={relatedCells} />
          {hasReasoning && (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-foreground">思考过程</h3>
              <AdminSafeMarkdown content={detail.reasoning_text ?? ''} className="text-sm" />
            </section>
          )}
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-foreground">模型输出</h3>
            {hasOutput ? (
              <AdminSafeMarkdown content={detail.output_text ?? ''} className="text-sm" />
            ) : (
              <p className="text-sm text-muted-foreground">本轮没有正文</p>
            )}
          </section>
        </div>
      ) : (
        <pre className="max-h-96 whitespace-pre-wrap break-words overflow-auto rounded-md border border-border/60 bg-muted/20 p-3 text-xs text-foreground">
          <code>{JSON.stringify({
            llm_round_id: detail.llm_round_id,
            reasoning_text: hasReasoning ? detail.reasoning_text : null,
            output_text: hasOutput ? detail.output_text : null,
            output_provenance: detail.output_provenance ?? null,
            context_visibility: detail.context_visibility ?? null,
          }, null, 2)}</code>
        </pre>
      )}
      <DetailWarnings redactedFields={redactedFields} truncatedFields={truncatedFields} />
    </div>
  );
}

function DetailWarnings({
  redactedFields,
  truncatedFields,
}: {
  redactedFields: readonly string[];
  truncatedFields: readonly string[];
}) {
  return (
    <>
      {redactedFields.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn/5 p-3 text-sm text-foreground">
          <p className="font-medium text-warn">部分字段已脱敏</p>
          <ul className="mt-1 list-disc pl-5 font-mono text-xs">
            {redactedFields.map(field => <li key={field}>{field}</li>)}
          </ul>
        </div>
      )}
      {truncatedFields.length > 0 && (
        <div className="rounded-md border border-warn/30 bg-warn/5 p-3 text-sm text-foreground">
          <p className="font-medium text-warn">部分正文已截断</p>
          <ul className="mt-1 list-disc pl-5 font-mono text-xs">
            {truncatedFields.map(field => <li key={field}>{field}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

function remoteSectionValue(
  response: TrajectoryNodeDetailResponse,
  section: RemoteDetailSectionName,
): Record<string, unknown> | null {
  if (!response.detail) return null;
  if (response.node_type === 'tool' && 'tool_call_id' in response.detail) {
    if (section === 'payload') return response.detail.payload;
    if (section === 'result') return response.detail.result;
    return null;
  }
  return null;
}

function RetryButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-md border border-border px-3 py-1.5 text-sm text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
    >
      {label}
    </button>
  );
}
