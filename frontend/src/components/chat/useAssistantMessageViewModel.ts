'use client';

import { useMemo } from 'react';

import { useAppSelector } from '@/redux/hooks';
import { selectStreamContentBlocks, selectStreamSlot } from '@/redux/slices/streamSlice';
import type { AgentEvidenceItem, AgentRunState, DocumentDraftState } from '@/types/agentRun';
import type {
  ContentBlock,
  DataSourceBlock,
  DocumentBlock,
  GeneratedImageBlock,
  ImageResultItem,
  VideoResultItem,
  KnowledgeEvidenceBlock,
  Message,
  SearchBlock,
  SearchSourceSummary,
  SourceReference,
  StructuredToolResultBlock,
  UrlBlock,
} from '@/types/conversation';
import { extractTextFromBlocks, extractThinkingFromBlocks } from '@/types/conversation';
import { buildThinkingTrace, type ThinkingTraceEntry } from './thinkingTrace';
import { buildAgentProcessRows, type AgentProcessRow } from './agentProcessModel';
import {
  collectStructuredToolResultBlocks,
  isStructuredToolResultBlock,
} from '@/lib/chat/structuredToolResults';

import { deriveAssistantActivity } from './assistantActivity';
import type { AssistantActivity } from './assistantActivity';
import { deriveAnswerEvidence } from './answerEvidenceModel';
import type { AnswerEvidenceModel } from './answerEvidenceModel';

interface UseAssistantMessageViewModelOptions {
  message: Message;
  modelId?: string | null;
  providerId?: string | null;
  isStreaming: boolean;
  isLastMessage: boolean;
  isLoadingQuestions: boolean;
  suggestedQuestionsCount: number;
  currentRun?: AgentRunState | null;
  /** 读哪个会话的流槽位。槽位按会话索引，必须说清楚是哪一条；
   *  不传时退回 message.chatId。 */
  conversationId?: string | null;
}

export interface AssistantMessageViewModel {
  blocksToRender: ContentBlock[];
  isCurrentlyStreaming: boolean;
  activity: AssistantActivity;
  searchSources: SearchSourceSummary[];
  searchQueries: string[];
  answerEvidence: AnswerEvidenceModel | null;
  knowledgeBlocks: KnowledgeEvidenceBlock[];
  structuredResults: StructuredToolResultBlock[];
  rawStructuredResults?: StructuredToolResultBlock[];
  documentBlocks?: DocumentBlock[];
  generatedImages?: GeneratedImageBlock[];
  /** image_search 找到的候选图；回答正文按 id 引用。 */
  answerImages?: ImageResultItem[];
  /** video_search 找到的候选视频；回答正文按 id 引用。 */
  answerVideos?: VideoResultItem[];
  /** 本条回答用到的地图数据服务商（按首次出现排序、去重），如 ['高德地图', '腾讯地图']。 */
  mapDataSources?: string[];
  documentDraft?: DocumentDraftState | null;
  displayText: string;
  displayThinking: string;
  /** 多轮思考与工具调用按发生顺序穿插，供深度思考面板展开时展示。 */
  thinkingTrace: ThinkingTraceEntry[];
  /** 有运行记录时的过程行：思考、工具调用、换模型按发生顺序排列。 */
  processRows: AgentProcessRow[];
  hasThinking: boolean;
  streamingStartTime: number | null;
  streamingEndTime: number | undefined;
  isStreamingReasoning: boolean;
  isThinkingPhaseComplete: boolean;
}

interface StaticAssistantMessageViewModelOptions {
  message: Message;
  isLoadingQuestions: boolean;
  suggestedQuestionsCount: number;
  currentRun?: AgentRunState | null;
}

export function deriveStaticAssistantMessageViewModel({
  message,
  isLoadingQuestions,
  suggestedQuestionsCount,
  currentRun = null,
}: StaticAssistantMessageViewModelOptions): AssistantMessageViewModel {
  const blocksToRender = message.content;
  const ownedRun = currentRun?.messageId === message.id || currentRun?.serverMessageId === message.id
    ? currentRun
    : null;
  const activity = deriveAssistantActivity({
    isStreaming: false,
    isCurrentlyStreaming: false,
    contentBlocks: blocksToRender,
    currentRun: ownedRun,
    messageStatus: message.status ?? null,
    isLoadingSuggestedQuestions: isLoadingQuestions,
    suggestedQuestionsCount,
  });
  const searchBlocks = collectSearchBlocks(blocksToRender);
  const knowledgeBlocks = collectKnowledgeEvidenceBlocks(blocksToRender);
  const toolBlocks = collectToolSourceBlocks(blocksToRender);
  const evidenceSearchSources = collectSearchSources(searchBlocks);
  const searchQueries = collectSearchQueries(searchBlocks);
  const displayText = extractTextFromBlocks(blocksToRender);
  const answerEvidence = deriveAnswerEvidence({
    sourceRefs: collectSourceRefs(searchBlocks, activity.urlBlocks),
    searchSources: evidenceSearchSources,
    urlBlocks: activity.urlBlocks,
    knowledgeBlocks,
    toolBlocks,
    agentEvidence: ownedRun?.evidence,
    answerText: displayText,
    searchProvider: collectSearchProvider(searchBlocks),
  });
  const searchSources = collectCitationSources(
    searchBlocks,
    activity.urlBlocks,
    evidenceSearchSources,
    answerEvidence,
    ownedRun?.evidence,
  );
  const displayThinking = extractThinkingFromBlocks(blocksToRender);
  const structuredResults = collectStructuredToolResultBlocks(blocksToRender);
  const rawStructuredResults = blocksToRender.filter(isStructuredToolResultBlock);
  const documentBlocks = collectDocumentBlocks(blocksToRender);
  const generatedImages = collectGeneratedImageBlocks(blocksToRender);
  const answerImages = collectAnswerImages(blocksToRender);
  const answerVideos = collectAnswerVideos(blocksToRender);
  const mapDataSources = collectMapDataSources(blocksToRender);
  const thinkingTrace = buildThinkingTrace(blocksToRender, ownedRun);
  const processRows = ownedRun ? buildAgentProcessRows(blocksToRender, ownedRun) : [];

  return {
    blocksToRender,
    isCurrentlyStreaming: false,
    activity,
    searchSources,
    searchQueries,
    answerEvidence,
    knowledgeBlocks,
    structuredResults,
    rawStructuredResults,
    documentBlocks,
    generatedImages,
    answerImages,
    answerVideos,
    mapDataSources,
    documentDraft: null,
    displayText,
    displayThinking,
    thinkingTrace,
    processRows,
    hasThinking: displayThinking.length > 0,
    streamingStartTime: null,
    streamingEndTime: undefined,
    isStreamingReasoning: false,
    isThinkingPhaseComplete: false,
  };
}

export function useAssistantMessageViewModel({
  message,
  isStreaming,
  isLastMessage,
  isLoadingQuestions,
  suggestedQuestionsCount,
  currentRun,
  conversationId,
}: UseAssistantMessageViewModelOptions): AssistantMessageViewModel {
  // 这条消息属于哪个会话，就读哪个会话的槽位——此前读的是全局那一份，
  // 另一个会话在生成时会把它的推理计时和正文串到这条消息上。
  const slotConversationId = conversationId ?? message.chatId ?? null;
  const streamingStartTime = useAppSelector(state => selectStreamSlot(state, slotConversationId).reasoningStartTime);
  const streamingEndTime = useAppSelector(state => selectStreamSlot(state, slotConversationId).reasoningEndTime);
  const isStreamingReasoning = useAppSelector(state => selectStreamSlot(state, slotConversationId).isStreamingReasoning);
  const isThinkingPhaseComplete = useAppSelector(state => selectStreamSlot(state, slotConversationId).isThinkingPhaseComplete);

  const streamBlocks = useAppSelector(state => {
    const slot = selectStreamSlot(state, slotConversationId);
    return isStreaming && (slot.messageId === message.id || (!slot.messageId && isLastMessage))
      ? selectStreamContentBlocks(slot)
      : null;
  });
  const blocksToRender: ContentBlock[] = (isStreaming && streamBlocks)
    ? streamBlocks
    : message.content;
  const isCurrentlyStreaming = isStreaming && streamBlocks !== null;

  const streamCurrentRun = useAppSelector(state => selectStreamSlot(state, slotConversationId).currentRun);
  const streamSearchSources = useAppSelector(state => selectStreamSlot(state, slotConversationId).searchSources);
  // 流槽位的错误只属于本会话最后一轮回答。
  const streamErrorMessage = useAppSelector(state => (
    isLastMessage ? selectStreamSlot(state, slotConversationId).lastError?.message ?? null : null
  ));
  const runForMessage = currentRun ?? streamCurrentRun;
  const ownedRun = runForMessage?.messageId === message.id || runForMessage?.serverMessageId === message.id
    ? runForMessage
    : null;

  const activity = useMemo(
    () => deriveAssistantActivity({
      isStreaming,
      isCurrentlyStreaming,
      contentBlocks: blocksToRender,
      currentRun: ownedRun,
      messageStatus: message.status ?? null,
      isLoadingSuggestedQuestions: isLoadingQuestions,
      suggestedQuestionsCount,
      streamErrorMessage,
    }),
    [
      isStreaming,
      isCurrentlyStreaming,
      blocksToRender,
      ownedRun,
      message.status,
      streamErrorMessage,
      isLoadingQuestions,
      suggestedQuestionsCount,
    ],
  );

  const searchBlocks = useMemo(
    () => collectSearchBlocks(blocksToRender),
    [blocksToRender],
  );

  const knowledgeBlocks = useMemo(
    () => collectKnowledgeEvidenceBlocks(blocksToRender),
    [blocksToRender],
  );

  const toolBlocks = useMemo(
    () => collectToolSourceBlocks(blocksToRender),
    [blocksToRender],
  );

  const evidenceSearchSources = useMemo(
    () => collectSearchSources(searchBlocks),
    [searchBlocks],
  );

  const searchQueries = useMemo(
    () => collectSearchQueries(searchBlocks),
    [searchBlocks],
  );

  const displayText = useMemo(() => extractTextFromBlocks(blocksToRender), [blocksToRender]);

  const answerEvidence = useMemo(
    () => {
      return deriveAnswerEvidence({
        sourceRefs: collectSourceRefs(searchBlocks, activity.urlBlocks),
        searchSources: evidenceSearchSources,
        urlBlocks: activity.urlBlocks,
        knowledgeBlocks,
        toolBlocks,
        agentEvidence: ownedRun?.evidence,
        answerText: displayText,
        searchProvider: collectSearchProvider(searchBlocks),
      });
    },
    [
      activity.urlBlocks,
      displayText,
      evidenceSearchSources,
      knowledgeBlocks,
      ownedRun?.evidence,
      searchBlocks,
      toolBlocks,
    ],
  );

  const searchSources: SearchSourceSummary[] = useMemo(
    () => collectCitationSources(
      searchBlocks,
      activity.urlBlocks,
      isCurrentlyStreaming && streamSearchSources.length > 0
        ? streamSearchSources
        : evidenceSearchSources,
      answerEvidence,
      ownedRun?.evidence,
    ),
    [
      activity.urlBlocks,
      answerEvidence,
      evidenceSearchSources,
      isCurrentlyStreaming,
      ownedRun?.evidence,
      searchBlocks,
      streamSearchSources,
    ],
  );

  const displayThinking = useMemo(() => extractThinkingFromBlocks(blocksToRender), [blocksToRender]);
  const structuredResults = useMemo(
    () => collectStructuredToolResultBlocks(blocksToRender),
    [blocksToRender],
  );
  const rawStructuredResults = useMemo(
    () => blocksToRender.filter(isStructuredToolResultBlock),
    [blocksToRender],
  );
  const documentBlocks = useMemo(() => collectDocumentBlocks(blocksToRender), [blocksToRender]);
  const generatedImages = useMemo(() => collectGeneratedImageBlocks(blocksToRender), [blocksToRender]);
  const answerImages = useMemo(() => collectAnswerImages(blocksToRender), [blocksToRender]);
  const answerVideos = useMemo(() => collectAnswerVideos(blocksToRender), [blocksToRender]);
  const mapDataSources = useMemo(() => collectMapDataSources(blocksToRender), [blocksToRender]);
  // 失败的草稿在回答结束后仍保留，让用户知道文档没有生成；成功的草稿由正式文档卡片替换。
  const documentDraft = isCurrentlyStreaming || ownedRun?.documentDraft?.failed
    ? ownedRun?.documentDraft ?? null
    : null;
  // 生成中按运行步骤排列，结束后按服务端块顺序；调用过工具的回答也展示思考。
  const thinkingTrace = useMemo(
    () => buildThinkingTrace(blocksToRender, ownedRun),
    [blocksToRender, ownedRun],
  );
  const processRows = useMemo(
    () => (ownedRun ? buildAgentProcessRows(blocksToRender, ownedRun) : []),
    [blocksToRender, ownedRun],
  );
  const hasThinking = displayThinking.length > 0;

  return {
    blocksToRender,
    isCurrentlyStreaming,
    activity,
    searchSources,
    searchQueries,
    answerEvidence,
    knowledgeBlocks,
    structuredResults,
    rawStructuredResults,
    documentBlocks,
    generatedImages,
    answerImages,
    answerVideos,
    mapDataSources,
    documentDraft,
    displayText,
    displayThinking,
    thinkingTrace,
    processRows,
    hasThinking,
    streamingStartTime,
    streamingEndTime,
    isStreamingReasoning,
    isThinkingPhaseComplete,
  };
}

function collectSearchBlocks(contentBlocks: ContentBlock[]): SearchBlock[] {
  return contentBlocks.filter((block): block is SearchBlock => block.type === 'search');
}

function collectDocumentBlocks(contentBlocks: ContentBlock[]): DocumentBlock[] {
  return contentBlocks.filter((block): block is DocumentBlock => block.type === 'document');
}

function collectAnswerImages(contentBlocks: ContentBlock[]): ImageResultItem[] {
  return contentBlocks.flatMap(block => (block.type === 'image_results' ? block.images : []));
}

function collectAnswerVideos(contentBlocks: ContentBlock[]): VideoResultItem[] {
  return contentBlocks.flatMap(block => (block.type === 'video_results' ? block.videos : []));
}

function collectGeneratedImageBlocks(contentBlocks: ContentBlock[]): GeneratedImageBlock[] {
  return contentBlocks.filter((block): block is GeneratedImageBlock => block.type === 'generated_image');
}

/** 地图来源：腾讯等通用 MCP 只留 data_source 署名，高德产品工具的地点/路线卡片自带 attribution。 */
function collectMapDataSources(contentBlocks: ContentBlock[]): string[] {
  const labels: string[] = [];
  for (const block of contentBlocks) {
    let label: string | null | undefined;
    if (block.type === 'data_source') {
      label = block.category === 'map' ? block.label : null;
    } else if (block.type === 'place_results' || block.type === 'route_results') {
      label = block.attribution?.label;
    }
    const trimmed = label?.trim();
    if (trimmed && !labels.includes(trimmed)) labels.push(trimmed);
  }
  return labels;
}

function collectKnowledgeEvidenceBlocks(contentBlocks: ContentBlock[]): KnowledgeEvidenceBlock[] {
  return contentBlocks.filter(
    (block): block is KnowledgeEvidenceBlock => block.type === 'knowledge_evidence',
  );
}

/** MCP 工具调用的来源块（地图与其他工具都带一条可引用来源）。 */
function collectToolSourceBlocks(contentBlocks: ContentBlock[]): DataSourceBlock[] {
  return contentBlocks.filter(
    (block): block is DataSourceBlock => block.type === 'data_source' && (block.source_refs?.length ?? 0) > 0,
  );
}

function collectSearchSources(searchBlocks: SearchBlock[]): SearchSourceSummary[] {
  return dedupeSearchSources(searchBlocks.flatMap(block => block.sources ?? []));
}

function collectSearchQueries(searchBlocks: SearchBlock[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];

  for (const block of searchBlocks) {
    const query = block.query.trim();
    if (!query || seen.has(query)) continue;
    seen.add(query);
    result.push(query);
  }

  return result;
}

function collectSearchProvider(searchBlocks: SearchBlock[]): string | undefined {
  return searchBlocks
    .map(block => block.result_provider?.trim())
    .find(Boolean);
}

function collectCitationSources(
  searchBlocks: SearchBlock[],
  urlBlocks: UrlBlock[],
  fallbackSources: SearchSourceSummary[],
  answerEvidence: AnswerEvidenceModel | null,
  agentEvidence: AgentEvidenceItem[] = [],
): SearchSourceSummary[] {
  const stableItems = [
    ...(answerEvidence?.usedItems ?? answerEvidence?.items ?? []),
    ...(answerEvidence?.candidateItems ?? []),
  ].filter(item => item.citationIndex != null);

  const sourceRefs = [
    ...searchBlocks.flatMap(block => block.source_refs ?? []),
    ...urlBlocks.flatMap(block => block.source_refs ?? []),
  ];
  // 正文引用映射独立于摘要展示；以持久化编号为准，补充知识库、工具等非网页来源。
  const byCitation = new Map<number, SearchSourceSummary>();
  for (const item of stableItems) {
    byCitation.set(item.citationIndex!, {
      title: item.title, url: item.url, favicon: item.favicon,
      evidence_id: item.evidenceId, citation_index: item.citationIndex,
      kind: item.kind === 'knowledge' || item.kind === 'tool' ? item.kind : 'web',
    });
  }
  for (const ref of sourceRefs.filter(isUsableSourceRef)) {
    if (ref.citation_index == null) continue;
    const existing = byCitation.get(ref.citation_index);
    byCitation.set(ref.citation_index, {
      ...existing,
      title: existing?.title ?? ref.title, url: ref.url,
      favicon: existing?.favicon ?? ref.favicon,
      evidence_id: ref.evidence_id, citation_index: ref.citation_index, kind: 'web',
    });
  }
  if (byCitation.size > 0) {
    return [...byCitation.values()]
      .sort((left, right) => left.citation_index! - right.citation_index!)
      .map(source => {
        // 完整引用注册表覆盖后，才按最终来源身份取摘要，不能沿用被替换来源的摘要。
        const evidence = source.kind === 'knowledge' || source.kind === 'tool' ? undefined : agentEvidence.find(entry => entry.kind === 'web'
          && entry.url === source.url
          && (source.evidence_id ? entry.id === source.evidence_id : entry.citationIndex === source.citation_index));
        return evidence?.snippet?.trim() ? { ...source, snippet: evidence.snippet.trim() } : source;
      });
  }
  if (sourceRefs.length === 0) {
    return fallbackSources;
  }

  const faviconFallbacks = new Map(fallbackSources.map(source => [source.url, source.favicon]));
  return dedupeSearchSources(
    sourceRefs
      .filter(ref => ref.kind === 'search' && isUsableSourceRef(ref))
      .map(ref => ({
        title: ref.title,
        url: ref.url,
        favicon: ref.favicon ?? faviconFallbacks.get(ref.url),
      })),
  );
}

function collectSourceRefs(
  searchBlocks: SearchBlock[],
  urlBlocks: UrlBlock[],
): SourceReference[] | undefined {
  const sourceRefs = [
    ...searchBlocks.flatMap(block => block.source_refs ?? []),
    ...urlBlocks.flatMap(block => block.source_refs ?? []),
  ];

  return sourceRefs.length > 0 ? sourceRefs : undefined;
}

function isUsableSourceRef(source: SourceReference): boolean {
  return Boolean(source.url?.trim()) && (source.status == null || source.status === 'success');
}

function dedupeSearchSources(sources: SearchSourceSummary[]): SearchSourceSummary[] {
  const seen = new Set<string>();
  const result: SearchSourceSummary[] = [];

  for (const source of sources) {
    const key = source.url || source.title;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(source);
  }

  return result;
}
