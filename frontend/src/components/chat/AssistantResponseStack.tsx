'use client';

import { memo, useMemo } from 'react';
import type {
  DocumentBlock,
  GeneratedImageBlock,
  ImageResultItem,
  VideoResultItem,
  SearchSourceSummary,
  StructuredToolResultBlock,
} from '@/types/conversation';
import type { AgentRunState, DocumentDraftState } from '@/types/agentRun';
import type { TrajectoryRunSummary } from '@/types/trajectory';
import type { TrajectoryBadgeStatus } from '@/lib/trajectory/TrajectoryCellProjection';
import ReasoningContent from './ReasoningContent';
import type { ThinkingTraceEntry } from './thinkingTrace';
import AssistantActivityStatus from './AssistantActivityStatus';
import type { AssistantActivity } from './assistantActivity';
import AnswerEvidence from './AnswerEvidence';
import type { AnswerEvidenceModel } from './answerEvidenceModel';
import type { AnswerEvidenceSidebarModel } from './answerEvidenceSidebarModel';
import MarkdownRenderer from './MarkdownRenderer';
import StructuredToolResults from './StructuredToolResults';
import DocumentCards from '@/components/documents/DocumentCards';
import DocumentDraftCard from '@/components/documents/DocumentDraftCard';
import GeneratedImages from './GeneratedImages';
import { collectPendingImageGenerations } from './generatedImageModel';
import AgentProcess from './AgentProcess';
import type { AgentProcessRow } from './agentProcessModel';

interface AssistantResponseStackProps {
  reasoning: {
    shouldRender: boolean;
    content: string;
    trace?: readonly ThinkingTraceEntry[];
    isVisible: boolean;
    isStreaming: boolean;
    onToggle: () => void;
    startTime?: number;
    endTime?: number;
  };
  activity: AssistantActivity;
  agentRun?: AgentRunState | null;
  /** 有运行记录时，思考与工具调用按步骤合成过程区，替代单独的思考面板。 */
  processRows?: readonly AgentProcessRow[];
  trajectoryRunSummary?: TrajectoryRunSummary;
  trajectoryStatus?: TrajectoryBadgeStatus;
  onInspectTrajectory?: () => void;
  answerEvidence: AnswerEvidenceModel | null;
  structuredResults?: StructuredToolResultBlock[];
  structuredResultsLoading?: boolean;
  documentBlocks?: DocumentBlock[];
  generatedImages?: GeneratedImageBlock[];
  mapDataSources?: string[];
  documentDraft?: DocumentDraftState | null;
  /** 回答在本页生成结束时自动打开产出的文档。 */
  autoOpenDocument?: boolean;
  onStructuredResultFollowUp?: (question: string) => void;
  answerEvidenceSidebar?: AnswerEvidenceSidebarModel | null;
  onSourceClick: (index: number) => void;
  onOpenSources: () => void;
  markdown: {
    content: string;
    sources: SearchSourceSummary[];
    onCitationClick?: (index: number) => void;
    images?: ImageResultItem[];
    videos?: VideoResultItem[];
  };
  showStreamingCursor: boolean;
}

const EMPTY_DOCUMENT_BLOCKS: DocumentBlock[] = [];
const EMPTY_GENERATED_IMAGES: GeneratedImageBlock[] = [];
const EMPTY_MAP_DATA_SOURCES: string[] = [];
const EMPTY_PROCESS_ROWS: AgentProcessRow[] = [];
// 地图来源只在回答结束后标注，生成中（含思考、调用工具阶段）不提前出现；HTML 代码块也只在结束后可运行。
const TERMINAL_ACTIVITY_KINDS: ReadonlySet<AssistantActivity['kind']> = new Set(['completed', 'failed', 'interrupted']);

function AssistantResponseStack({
  reasoning,
  activity,
  agentRun,
  processRows = EMPTY_PROCESS_ROWS,
  trajectoryRunSummary,
  trajectoryStatus = 'unknown',
  onInspectTrajectory,
  answerEvidence,
  structuredResults = [],
  structuredResultsLoading = false,
  documentBlocks = EMPTY_DOCUMENT_BLOCKS,
  generatedImages = EMPTY_GENERATED_IMAGES,
  mapDataSources = EMPTY_MAP_DATA_SOURCES,
  documentDraft = null,
  autoOpenDocument = false,
  onStructuredResultFollowUp,
  answerEvidenceSidebar,
  onSourceClick,
  onOpenSources,
  markdown,
  showStreamingCursor,
}: AssistantResponseStackProps) {
  const showReasoning = reasoning.shouldRender;
  // 有运行记录时思考在过程区里展示，过程区展开且已有思考行才算“思考可见”，否则状态栏照常提示模型阶段。
  const reasoningVisible = agentRun
    ? reasoning.isVisible && processRows.some(row => row.kind === 'thinking')
    : showReasoning;
  const pendingImages = useMemo(
    () => collectPendingImageGenerations(agentRun, generatedImages),
    [agentRun, generatedImages],
  );
  const stopAwaitingConfirmation = agentRun?.status === 'running' && Boolean(agentRun.stopConfirmation);
  // 任何新的模型输出都会改变该标记，底部状态据此判断模型是否长时间没有动静。
  const outputMark = [
    activity.kind,
    markdown.content.length,
    reasoning.content.length,
    documentDraft?.content.length ?? 0,
    structuredResults.length,
    agentRun?.toolCallProgress ?? 0,
  ].join(':');
  // 模型只输出了思考、没写正文时如实说明，不拿思考冒充回答。
  const answerMissing = activity.kind === 'completed'
    && !activity.hasText
    && structuredResults.length === 0
    && documentBlocks.length === 0
    && generatedImages.length === 0
    && !documentDraft;

  return (
    <div
      data-testid="assistant-response-stack"
      className="w-full min-w-0 [&>*:last-child]:mb-0"
    >
      <div className="w-full max-w-6xl">
        {agentRun ? (
          <AgentProcess
            run={agentRun}
            rows={processRows}
            thinkingStreaming={reasoning.isStreaming}
            isVisible={reasoning.isVisible}
            onToggle={reasoning.onToggle}
            runSummary={trajectoryRunSummary}
            trajectoryStatus={trajectoryStatus}
            onInspect={onInspectTrajectory}
          />
        ) : showReasoning ? (
          <ReasoningContent
            content={reasoning.content}
            trace={reasoning.trace}
            isVisible={reasoning.isVisible}
            onToggle={reasoning.onToggle}
            isStreaming={reasoning.isStreaming}
            startTime={reasoning.startTime}
            endTime={reasoning.endTime}
          />
        ) : null}

        <AssistantActivityStatus
          activity={activity}
          placement="top"
          reasoningVisible={reasoningVisible}
          stopPending={stopAwaitingConfirmation}
          toolIssuesInProcess={Boolean(agentRun)}
        />
      </div>

      <StructuredToolResults
        blocks={structuredResults}
        isLoading={structuredResultsLoading}
        onFollowUp={onStructuredResultFollowUp}
      />

      {documentDraft ? <DocumentDraftCard draft={documentDraft} /> : null}

      <DocumentCards
        blocks={documentBlocks}
        autoOpen={autoOpenDocument && activity.kind === 'completed'}
      />

      <GeneratedImages
        blocks={generatedImages}
        pending={pendingImages}
        onRegenerate={onStructuredResultFollowUp}
      />

      <div className="w-full max-w-6xl">
        <AnswerEvidence
          evidence={answerEvidence}
          onSourceClick={onSourceClick}
          onOpenSources={onOpenSources}
          hasSidebarContent={Boolean(answerEvidenceSidebar?.isRenderable)}
          sidebarIssueCount={answerEvidenceSidebar?.summary.issueCount ?? 0}
        />

        <MarkdownRenderer
          content={markdown.content}
          sources={markdown.sources}
          onCitationClick={markdown.onCitationClick}
          images={markdown.images}
          videos={markdown.videos}
          codeRunReady={TERMINAL_ACTIVITY_KINDS.has(activity.kind)}
        />

        {mapDataSources.length > 0 && TERMINAL_ACTIVITY_KINDS.has(activity.kind) ? (
          <p data-testid="assistant-map-data-sources" className="mt-2 text-xs text-muted-foreground">
            地图数据由 {mapDataSources.join('、')} 提供
          </p>
        ) : null}

        {answerMissing ? (
          <p data-testid="assistant-answer-missing" className="text-sm text-muted-foreground">
            模型这次没有给出回答。
          </p>
        ) : null}

        {showStreamingCursor ? (
          <span
            data-testid="streaming-cursor"
            className="animate-pulse motion-reduce:animate-none"
          >
            ▌
          </span>
        ) : null}

        {/* 进行中的状态跟在最新内容之后，避免用户要滚回顶部才能看到进度。 */}
        <AssistantActivityStatus
          activity={activity}
          placement="bottom"
          reasoningVisible={reasoningVisible}
          stopPending={stopAwaitingConfirmation}
          outputMark={outputMark}
        />
      </div>
    </div>
  );
}

export default memo(AssistantResponseStack);
