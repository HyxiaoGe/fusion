'use client';

import { memo } from 'react';
import type { DocumentBlock, SearchSourceSummary, StructuredToolResultBlock } from '@/types/conversation';
import type { AgentRunState, DocumentDraftState } from '@/types/agentRun';
import type { TrajectoryRunSummary } from '@/types/trajectory';
import type { TrajectoryBadgeStatus } from '@/lib/trajectory/TrajectoryCellProjection';
import ReasoningContent from './ReasoningContent';
import AssistantActivityStatus from './AssistantActivityStatus';
import type { AssistantActivity } from './assistantActivity';
import AnswerEvidence from './AnswerEvidence';
import type { AnswerEvidenceModel } from './answerEvidenceModel';
import type { AnswerEvidenceSidebarModel } from './answerEvidenceSidebarModel';
import MarkdownRenderer from './MarkdownRenderer';
import StructuredToolResults from './StructuredToolResults';
import DocumentCards from '@/components/documents/DocumentCards';
import DocumentDraftCard from '@/components/documents/DocumentDraftCard';
import TrajectoryStatusLine from './trajectory/TrajectoryStatusLine';

interface AssistantResponseStackProps {
  reasoning: {
    shouldRender: boolean;
    content: string;
    isVisible: boolean;
    isStreaming: boolean;
    onToggle: () => void;
    startTime?: number;
    endTime?: number;
  };
  activity: AssistantActivity;
  agentRun?: AgentRunState | null;
  trajectoryRunSummary?: TrajectoryRunSummary;
  trajectoryStatus?: TrajectoryBadgeStatus;
  onInspectTrajectory?: () => void;
  answerEvidence: AnswerEvidenceModel | null;
  structuredResults?: StructuredToolResultBlock[];
  structuredResultsLoading?: boolean;
  documentBlocks?: DocumentBlock[];
  documentDraft?: DocumentDraftState | null;
  onStructuredResultFollowUp?: (question: string) => void;
  answerEvidenceSidebar?: AnswerEvidenceSidebarModel | null;
  onSourceClick: (index: number) => void;
  onOpenSources: () => void;
  markdown: {
    content: string;
    sources: SearchSourceSummary[];
    onCitationClick?: (index: number) => void;
  };
  showStreamingCursor: boolean;
}

const EMPTY_DOCUMENT_BLOCKS: DocumentBlock[] = [];

function AssistantResponseStack({
  reasoning,
  activity,
  agentRun,
  trajectoryRunSummary,
  trajectoryStatus = 'unknown',
  onInspectTrajectory,
  answerEvidence,
  structuredResults = [],
  structuredResultsLoading = false,
  documentBlocks = EMPTY_DOCUMENT_BLOCKS,
  documentDraft = null,
  onStructuredResultFollowUp,
  answerEvidenceSidebar,
  onSourceClick,
  onOpenSources,
  markdown,
  showStreamingCursor,
}: AssistantResponseStackProps) {
  const showReasoning = reasoning.shouldRender;
  const stopAwaitingConfirmation = agentRun?.status === 'running' && Boolean(agentRun.stopConfirmation);

  return (
    <div
      data-testid="assistant-response-stack"
      className="w-full min-w-0 [&>*:last-child]:mb-0"
    >
      <div className="w-full max-w-6xl">
        {showReasoning ? (
          <ReasoningContent
            content={reasoning.content}
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
          reasoningVisible={showReasoning}
          stopPending={stopAwaitingConfirmation}
        />

        {agentRun ? (
          <TrajectoryStatusLine
            run={agentRun}
            runSummary={trajectoryRunSummary}
            trajectoryStatus={trajectoryStatus}
            onInspect={onInspectTrajectory}
          />
        ) : null}

      </div>

      <StructuredToolResults
        blocks={structuredResults}
        isLoading={structuredResultsLoading}
        onFollowUp={onStructuredResultFollowUp}
      />

      {documentDraft ? <DocumentDraftCard draft={documentDraft} /> : null}

      <DocumentCards blocks={documentBlocks} />

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
        />

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
          reasoningVisible={showReasoning}
          stopPending={stopAwaitingConfirmation}
        />
      </div>
    </div>
  );
}

export default memo(AssistantResponseStack);
