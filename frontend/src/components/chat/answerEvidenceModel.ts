import type { AgentEvidenceItem } from '@/types/agentRun';
import type {
  DataSourceBlock,
  KnowledgeEvidenceBlock,
  KnowledgeSourceReference,
  SearchSourceSummary,
  SourceReference,
  ToolSourceReference,
  UrlBlock,
} from '@/types/conversation';

interface BaseAnswerEvidenceItem {
  id: string;
  evidenceId?: string;
  citationIndex?: number;
  title: string;
  url: string;
  domain: string;
  favicon?: string;
}

type SearchAnswerEvidenceItem = BaseAnswerEvidenceItem & {
  kind: 'search_source';
  sourceIndex: number;
  deepRead?: boolean;
};

type UrlReadAnswerEvidenceItem = BaseAnswerEvidenceItem & {
  kind: 'url_read';
};

export type KnowledgeAnswerEvidenceItem = BaseAnswerEvidenceItem & {
  kind: 'knowledge';
  sourceIndex: number;
  knowledgeBaseId: string;
  knowledgeBaseName: string;
  documentId: string;
  indexVersion: string;
  chunkId: string;
  ordinal: number;
  filename: string;
  page: number | null;
  section: string | null;
  charStart: number;
  charEnd: number;
  /** 依据条按文件合并时，同一文件被合并的段落数。 */
  documentChunkCount?: number;
};

/** 一次外部工具调用（MCP）的结果；没有网址，title 是「服务 / 工具」，domain 是服务名。 */
export type ToolAnswerEvidenceItem = BaseAnswerEvidenceItem & {
  kind: 'tool';
  sourceIndex: number;
  provider: string;
};

export type AnswerEvidenceItem =
  | SearchAnswerEvidenceItem
  | UrlReadAnswerEvidenceItem
  | KnowledgeAnswerEvidenceItem
  | ToolAnswerEvidenceItem;

/** 没有网址、靠稳定 evidence_id 定位的来源。 */
type LocalAnswerEvidenceItem = KnowledgeAnswerEvidenceItem | ToolAnswerEvidenceItem;

export interface AnswerEvidenceModel {
  items: AnswerEvidenceItem[];
  previewItems: AnswerEvidenceItem[];
  usedItems?: AnswerEvidenceItem[];
  candidateItems?: AnswerEvidenceItem[];
  usedCount?: number;
  candidateCount?: number;
  searchCount: number;
  urlCount: number;
  knowledgeCount?: number;
  toolCount?: number;
  totalCount: number;
  hiddenSearchCount: number;
  hiddenUrlCount: number;
  summary: string;
  hasSearchSources: boolean;
}

interface DeriveAnswerEvidenceInput {
  sourceRefs?: SourceReference[];
  searchSources: SearchSourceSummary[];
  urlBlocks: UrlBlock[];
  knowledgeBlocks?: KnowledgeEvidenceBlock[];
  toolBlocks?: DataSourceBlock[];
  agentEvidence?: AgentEvidenceItem[] | null;
  answerText?: string;
  searchProvider?: string | null;
  previewLimit?: number;
}

export function deriveAnswerEvidence(input: DeriveAnswerEvidenceInput): AnswerEvidenceModel | null {
  const localEvidence = combineLocalEvidence(
    partitionLocalEvidence(
      deriveKnowledgeEvidenceItems(input.knowledgeBlocks ?? []),
      input.agentEvidence ?? [],
      input.answerText ?? '',
      'knowledge',
    ),
    partitionLocalEvidence(
      deriveToolEvidenceItems(input.toolBlocks ?? []),
      input.agentEvidence ?? [],
      input.answerText ?? '',
      'tool',
    ),
  );
  const agentEvidenceModel = deriveAgentEvidenceModel(input);
  if (agentEvidenceModel) {
    return mergeLocalEvidence(agentEvidenceModel, localEvidence);
  }

  const useSourceRefs = Boolean(input.sourceRefs && input.sourceRefs.length > 0);
  const sourceRefItems = input.sourceRefs?.filter(isUsableSourceRef) ?? [];
  const unifiedItems = useSourceRefs
    ? toSourceRefEvidenceItems(sourceRefItems, buildFaviconFallbacks(input.searchSources, input.urlBlocks))
    : null;
  const legacyItems = unifiedItems ? null : toLegacyEvidenceItems(input.searchSources, input.urlBlocks);
  const searchItems = unifiedItems?.searchItems ?? legacyItems?.searchItems ?? [];
  const urlItems = unifiedItems?.urlItems ?? legacyItems?.urlItems ?? [];
  const urlCount = unifiedItems?.urlCount ?? legacyItems?.urlCount ?? urlItems.length;
  const items = unifiedItems?.items ?? [...searchItems, ...urlItems];

  if (items.length === 0) {
    return buildLocalOnlyEvidence(localEvidence);
  }

  const citedIndexes = collectExplicitCitationIndexes(input.answerText ?? '');
  const hasStableIndexes = items.some(item => item.citationIndex != null);
  const usedItems = hasStableIndexes
    ? items.filter(item => item.citationIndex != null && citedIndexes.has(item.citationIndex))
    : items;
  const candidateItems = items.filter(item => !usedItems.includes(item));
  const primaryItems = usedItems.length > 0 ? usedItems : candidateItems;
  return mergeLocalEvidence({
    items: primaryItems,
    previewItems: primaryItems,
    usedItems,
    candidateItems,
    usedCount: usedItems.length,
    candidateCount: candidateItems.length,
    searchCount: searchItems.length,
    urlCount,
    knowledgeCount: 0,
    totalCount: items.length,
    hiddenSearchCount: 0,
    hiddenUrlCount: 0,
    summary: buildSummary(searchItems.length, urlCount, deriveSearchProviderLabel(input.searchProvider)),
    hasSearchSources: searchItems.length > 0,
  }, localEvidence);
}

function deriveAgentEvidenceModel(input: DeriveAnswerEvidenceInput): AnswerEvidenceModel | null {
  const evidence = input.agentEvidence?.filter(isRenderableAgentWebEvidence) ?? [];
  if (evidence.length === 0) {
    return null;
  }

  // Agent 摘要有数量上限；完整 source_refs 才是引用注册表。
  const citedIndexes = collectExplicitCitationIndexes(input.answerText ?? '');
  for (const ref of input.sourceRefs?.filter(isUsableSourceRef) ?? []) {
    const existing = evidence.find(item => (
      (ref.evidence_id && item.id === ref.evidence_id) || normalizeUrlKey(item.url) === normalizeUrlKey(ref.url)
    ));
    const citationIndex = ref.citation_index ?? existing?.citationIndex;
    const usedByCitation = citationIndex != null && citedIndexes.has(citationIndex);
    if (existing) {
      const index = evidence.indexOf(existing);
      evidence[index] = {
        ...existing,
        citationIndex,
        usedByFinalAnswer: existing.usedByFinalAnswer || usedByCitation,
      };
    } else {
      evidence.push({
        id: ref.evidence_id || `source-ref-${normalizeUrlKey(ref.url)}`,
        kind: 'web',
        status: ref.kind === 'url_read' ? 'read_success' : 'candidate',
        title: ref.title,
        url: ref.url,
        citationIndex,
        claim: '',
        usedByFinalAnswer: usedByCitation,
      });
    }
  }
  const context = buildAgentEvidenceContext(input, evidence);
  const usedEvidence = evidence.filter(item => item.usedByFinalAnswer || item.status === 'used');
  const usedItems = sortEvidenceItemsByCitation(
    dedupeEvidenceItems(usedEvidence.map((item, index) => toAgentEvidenceItem(item, index, context))),
  );
  const usedKeys = new Set(usedItems.map(item => normalizeUrlKey(item.url)).filter(Boolean));
  const candidateEvidence = evidence.filter(item => isCandidateAgentEvidence(item) && !usedKeys.has(normalizeUrlKey(item.url)));
  const candidateItems = sortEvidenceItemsByCitation(
    dedupeEvidenceItems(
      candidateEvidence.map((item, index) => toAgentEvidenceItem(item, index + usedItems.length, context)),
    ),
  );
  const primaryItems = usedItems.length > 0 ? usedItems : candidateItems;

  if (primaryItems.length === 0 && candidateItems.length === 0) {
    return null;
  }

  const allItems = [...usedItems, ...candidateItems];
  const searchCount = allItems.filter(item => item.kind === 'search_source').length;
  const urlCount = context.deepReadUrls.size;

  return {
    items: primaryItems,
    previewItems: primaryItems,
    usedItems,
    candidateItems,
    usedCount: usedItems.length,
    candidateCount: candidateItems.length,
    searchCount,
    urlCount,
    knowledgeCount: 0,
    totalCount: allItems.length,
    hiddenSearchCount: 0,
    hiddenUrlCount: 0,
    summary: buildAgentEvidenceSummary({
      usedCount: usedItems.length,
      candidateCount: candidateItems.length,
      urlCount,
      searchProviderLabel: deriveSearchProviderLabel(input.searchProvider),
    }),
    hasSearchSources: searchCount > 0,
  };
}

interface AgentEvidenceContext {
  searchIndexByUrl: Map<string, number>;
  faviconByUrl: Map<string, string>;
  deepReadUrls: Set<string>;
  citationIndexByEvidenceId: Map<string, number>;
  citationIndexByUrl: Map<string, number>;
}

function buildAgentEvidenceContext(
  input: DeriveAnswerEvidenceInput,
  evidence: AgentEvidenceItem[],
): AgentEvidenceContext {
  const searchIndexByUrl = new Map<string, number>();
  const faviconByUrl = new Map<string, string>();
  const deepReadUrls = new Set<string>();
  const citationIndexByEvidenceId = new Map<string, number>();
  const citationIndexByUrl = new Map<string, number>();
  let searchIndex = 0;

  const addSearch = (url: string | undefined, favicon?: string) => {
    const key = normalizeUrlKey(url);
    if (!key) return;
    if (!searchIndexByUrl.has(key)) {
      searchIndexByUrl.set(key, searchIndex);
      searchIndex += 1;
    }
    if (favicon?.trim() && !faviconByUrl.has(key)) {
      faviconByUrl.set(key, favicon);
    }
  };

  const addDeepRead = (url: string | undefined, favicon?: string) => {
    const key = normalizeUrlKey(url);
    if (!key) return;
    deepReadUrls.add(key);
    if (favicon?.trim() && !faviconByUrl.has(key)) {
      faviconByUrl.set(key, favicon);
    }
  };

  input.sourceRefs?.forEach(ref => {
    const urlKey = normalizeUrlKey(ref.url);
    if (ref.citation_index) {
      if (ref.evidence_id) {
        citationIndexByEvidenceId.set(ref.evidence_id, ref.citation_index);
      }
      if (urlKey) {
        citationIndexByUrl.set(urlKey, ref.citation_index);
      }
    }
    if (ref.kind === 'search' && isUsableSourceRef(ref)) {
      addSearch(ref.url, ref.favicon);
    } else if (ref.kind === 'url_read' && isUsableSourceRef(ref)) {
      addDeepRead(ref.url, ref.favicon);
    }
  });
  input.searchSources.forEach(source => addSearch(source.url, source.favicon));
  input.urlBlocks.filter(isSuccessfulUrlBlock).forEach(block => addDeepRead(block.url, block.favicon));
  evidence
    .filter(item => item.status === 'read_success')
    .forEach(item => addDeepRead(item.url, undefined));

  return {
    searchIndexByUrl,
    faviconByUrl,
    deepReadUrls,
    citationIndexByEvidenceId,
    citationIndexByUrl,
  };
}

function toAgentEvidenceItem(
  evidence: AgentEvidenceItem,
  fallbackIndex: number,
  context: AgentEvidenceContext,
): AnswerEvidenceItem {
  const url = evidence.url ?? '';
  const urlKey = normalizeUrlKey(url);
  const searchIndex = context.searchIndexByUrl.get(urlKey);
  const favicon = context.faviconByUrl.get(urlKey);
  const domain = normalizeDomain(evidence.domain, url);
  const base = {
    id: `agent-evidence-${evidence.id || fallbackIndex}`,
    evidenceId: evidence.id || undefined,
    citationIndex: evidence.citationIndex
      ?? context.citationIndexByEvidenceId.get(evidence.id)
      ?? context.citationIndexByUrl.get(urlKey),
    title: normalizeTitle(evidence.title, url),
    url,
    domain,
    favicon: normalizeFavicon(url, favicon),
  };

  if (evidence.status === 'read_success' || (searchIndex == null && context.deepReadUrls.has(urlKey))) {
    return {
      ...base,
      kind: 'url_read',
    };
  }

  return {
    ...base,
    kind: 'search_source',
    sourceIndex: searchIndex ?? fallbackIndex,
    deepRead: context.deepReadUrls.has(urlKey),
  };
}

function dedupeEvidenceItems(items: AnswerEvidenceItem[]): AnswerEvidenceItem[] {
  const deduped: AnswerEvidenceItem[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const key = item.evidenceId || normalizeUrlKey(item.url) || item.id;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }
  return deduped;
}

function sortEvidenceItemsByCitation(items: AnswerEvidenceItem[]): AnswerEvidenceItem[] {
  return items
    .map((item, stableIndex) => ({ item, stableIndex }))
    .sort((left, right) => {
      const leftCitation = left.item.citationIndex;
      const rightCitation = right.item.citationIndex;
      if (leftCitation != null && rightCitation != null) {
        return leftCitation - rightCitation || left.stableIndex - right.stableIndex;
      }
      if (leftCitation != null) return -1;
      if (rightCitation != null) return 1;
      return left.stableIndex - right.stableIndex;
    })
    .map(({ item }, index) => item.kind === 'search_source' || item.kind === 'knowledge' || item.kind === 'tool'
      ? { ...item, sourceIndex: index }
      : item);
}

function deriveKnowledgeEvidenceItems(
  blocks: KnowledgeEvidenceBlock[],
): KnowledgeAnswerEvidenceItem[] {
  const items = blocks.flatMap((block) => block.source_refs)
    .filter((source) => source.status == null || source.status === 'success')
    .map(toKnowledgeEvidenceItem);
  return sortEvidenceItemsByCitation(dedupeEvidenceItems(items))
    .filter((item): item is KnowledgeAnswerEvidenceItem => item.kind === 'knowledge');
}

function deriveToolEvidenceItems(blocks: DataSourceBlock[]): ToolAnswerEvidenceItem[] {
  const items = blocks.flatMap(block => (block.source_refs ?? []).map(source => toToolEvidenceItem(block, source)));
  return sortEvidenceItemsByCitation(dedupeEvidenceItems(items))
    .filter((item): item is ToolAnswerEvidenceItem => item.kind === 'tool');
}

interface LocalEvidencePartition<T extends LocalAnswerEvidenceItem = LocalAnswerEvidenceItem> {
  allItems: T[];
  usedItems: T[];
  candidateItems: T[];
}

/**
 * 有本类的 Agent 证据时以它为准，否则按回答正文里的引用编号判定是否用到。
 * 运行状态里的证据最多保留 12 条，被挤掉的条目没有对应证据，同样按正文编号判定。
 */
function partitionLocalEvidence<T extends LocalAnswerEvidenceItem>(
  items: T[],
  agentEvidence: AgentEvidenceItem[],
  answerText: string,
  kind: T['kind'],
): LocalEvidencePartition<T> {
  const kindAgentEvidence = agentEvidence.filter((item) => item.kind === kind);
  if (kindAgentEvidence.length > 0) {
    const citedIndexes = collectExplicitCitationIndexes(answerText);
    const usedItems = items.filter((item) => {
      const matched = kindAgentEvidence.filter((evidence) => matchesLocalEvidence(item, evidence));
      if (matched.length === 0) {
        return item.citationIndex != null && citedIndexes.has(item.citationIndex);
      }
      return matched.some((evidence) => evidence.usedByFinalAnswer || evidence.status === 'used');
    });
    const usedKeys = new Set(usedItems.map(localEvidenceKey));
    const candidateItems = items.filter((item) => (
      !usedKeys.has(localEvidenceKey(item))
      && !kindAgentEvidence.some((evidence) => (
        matchesLocalEvidence(item, evidence)
        && evidence.status === 'discarded'
      ))
    ));
    return {
      allItems: items,
      usedItems: sortLocalEvidenceItems(usedItems, kind),
      candidateItems: sortLocalEvidenceItems(candidateItems, kind),
    };
  }

  const citedIndexes = collectExplicitCitationIndexes(answerText);
  const usedItems = items.filter((item) => (
    item.citationIndex != null && citedIndexes.has(item.citationIndex)
  ));
  const usedKeys = new Set(usedItems.map(localEvidenceKey));
  return {
    allItems: items,
    usedItems: sortLocalEvidenceItems(usedItems, kind),
    candidateItems: sortLocalEvidenceItems(
      items.filter((item) => !usedKeys.has(localEvidenceKey(item))),
      kind,
    ),
  };
}

function combineLocalEvidence(...partitions: LocalEvidencePartition[]): LocalEvidencePartition {
  return {
    allItems: partitions.flatMap(partition => partition.allItems),
    usedItems: partitions.flatMap(partition => partition.usedItems),
    candidateItems: partitions.flatMap(partition => partition.candidateItems),
  };
}

function matchesLocalEvidence(
  item: LocalAnswerEvidenceItem,
  evidence: AgentEvidenceItem,
): boolean {
  return Boolean(
    (item.evidenceId && evidence.id === item.evidenceId)
    || (
      item.citationIndex != null
      && evidence.citationIndex != null
      && item.citationIndex === evidence.citationIndex
    ),
  );
}

function localEvidenceKey(item: LocalAnswerEvidenceItem): string {
  return item.evidenceId ?? item.id;
}

function sortLocalEvidenceItems<T extends LocalAnswerEvidenceItem>(items: T[], kind: T['kind']): T[] {
  return sortEvidenceItemsByCitation(items)
    .filter((item): item is T => item.kind === kind);
}

function collectExplicitCitationIndexes(answerText: string): Set<number> {
  const indexes = new Set<number>();
  const citationPattern = /\[(\d+)\]/g;
  let match: RegExpExecArray | null;
  while ((match = citationPattern.exec(answerText)) !== null) {
    const citationIndex = Number.parseInt(match[1], 10);
    if (citationIndex > 0) indexes.add(citationIndex);
  }
  return indexes;
}

function toKnowledgeEvidenceItem(source: KnowledgeSourceReference): KnowledgeAnswerEvidenceItem {
  return {
    id: source.evidence_id
      ? `knowledge-${source.evidence_id}`
      : `knowledge-${source.knowledge_base_id}-${source.document_id}-${source.index_version}-${source.chunk_id}`,
    evidenceId: source.evidence_id,
    citationIndex: source.citation_index,
    kind: 'knowledge',
    sourceIndex: 0,
    title: source.filename,
    url: '',
    domain: source.knowledge_base_name || '知识库',
    knowledgeBaseId: source.knowledge_base_id,
    knowledgeBaseName: source.knowledge_base_name,
    documentId: source.document_id,
    indexVersion: source.index_version,
    chunkId: source.chunk_id,
    ordinal: source.ordinal,
    filename: source.filename,
    page: source.page,
    section: source.section,
    charStart: source.char_start,
    charEnd: source.char_end,
  };
}

function toToolEvidenceItem(block: DataSourceBlock, source: ToolSourceReference): ToolAnswerEvidenceItem {
  return {
    id: `tool-${source.evidence_id}`,
    evidenceId: source.evidence_id,
    citationIndex: source.citation_index,
    kind: 'tool',
    sourceIndex: 0,
    title: source.title,
    url: '',
    domain: block.label,
    provider: source.provider,
  };
}

function countLocalEvidence(partition: LocalEvidencePartition, kind: LocalAnswerEvidenceItem['kind']): number {
  return partition.allItems.filter(item => item.kind === kind).length;
}

function buildLocalOnlyEvidence(
  localEvidence: LocalEvidencePartition,
): AnswerEvidenceModel | null {
  if (localEvidence.allItems.length === 0) return null;
  const usedItems = sortEvidenceItemsByCitation(localEvidence.usedItems);
  const candidateItems = sortEvidenceItemsByCitation(localEvidence.candidateItems);
  const items = usedItems.length > 0 ? usedItems : candidateItems;
  return {
    items,
    previewItems: items,
    usedItems,
    candidateItems,
    usedCount: usedItems.length,
    candidateCount: candidateItems.length,
    searchCount: 0,
    urlCount: 0,
    knowledgeCount: countLocalEvidence(localEvidence, 'knowledge'),
    toolCount: countLocalEvidence(localEvidence, 'tool'),
    totalCount: usedItems.length + candidateItems.length,
    hiddenSearchCount: 0,
    hiddenUrlCount: 0,
    summary: buildLocalSummary(usedItems.length, candidateItems.length),
    hasSearchSources: false,
  };
}

function mergeLocalEvidence(
  base: AnswerEvidenceModel,
  localEvidence: LocalEvidencePartition,
): AnswerEvidenceModel {
  if (localEvidence.allItems.length === 0) return base;
  const usedItems = sortEvidenceItemsByCitation([
    ...(base.usedItems ?? base.items),
    ...localEvidence.usedItems,
  ]);
  const candidateItems = sortEvidenceItemsByCitation([
    ...(base.candidateItems ?? []),
    ...localEvidence.candidateItems,
  ]);
  const items = usedItems.length > 0 ? usedItems : candidateItems;
  const knowledgeCount = countLocalEvidence(localEvidence, 'knowledge');
  const toolCount = countLocalEvidence(localEvidence, 'tool');
  const summaryParts = ['回答依据'];
  if (knowledgeCount > 0) summaryParts.push(`知识库 ${knowledgeCount} 条`);
  if (toolCount > 0) summaryParts.push(`工具数据 ${toolCount} 条`);
  if (base.searchCount > 0) summaryParts.push(`搜索候选 ${base.searchCount} 条`);
  if (base.urlCount > 0) summaryParts.push(`深读 ${base.urlCount} 个网页`);
  return {
    ...base,
    items,
    previewItems: items,
    usedItems,
    candidateItems,
    usedCount: usedItems.length,
    knowledgeCount,
    toolCount,
    totalCount: usedItems.length + candidateItems.length,
    summary: summaryParts.join(' · '),
  };
}

function buildLocalSummary(usedCount: number, candidateCount: number): string {
  const parts = ['回答依据'];
  if (usedCount > 0) parts.push(`已使用 ${usedCount} 条`);
  if (candidateCount > 0) parts.push(`候选 ${candidateCount} 条`);
  return parts.join(' · ');
}

function isRenderableAgentWebEvidence(item: AgentEvidenceItem): boolean {
  if (item.kind !== 'web' || !item.url?.trim()) {
    return false;
  }

  return item.status === 'used'
    || item.usedByFinalAnswer
    || item.status === 'candidate'
    || item.status === 'selected'
    || item.status === 'read_success';
}

function isCandidateAgentEvidence(item: AgentEvidenceItem): boolean {
  return item.status === 'candidate' || item.status === 'selected' || item.status === 'read_success';
}

function toSearchEvidenceItem(
  source: SearchSourceSummary,
  index: number,
  deepReadUrls: Set<string> = new Set(),
): SearchAnswerEvidenceItem {
  return {
    id: `search-${index}`,
    evidenceId: source.evidence_id,
    citationIndex: source.citation_index,
    kind: 'search_source',
    title: normalizeTitle(source.title, source.url),
    url: source.url,
    domain: deriveDomain(source.url),
    favicon: normalizeFavicon(source.url, source.favicon),
    sourceIndex: index,
    deepRead: deepReadUrls.has(normalizeUrlKey(source.url)),
  };
}

function toUrlEvidenceItem(block: UrlBlock): UrlReadAnswerEvidenceItem {
  return {
    id: `url-${block.id}`,
    kind: 'url_read',
    title: normalizeTitle(block.title, block.url),
    url: block.url,
    domain: deriveDomain(block.url),
    favicon: normalizeFavicon(block.url, block.favicon),
  };
}

function toSourceRefEvidenceItems(
  sourceRefs: SourceReference[],
  faviconFallbacks: FaviconFallbacks,
): {
  items: AnswerEvidenceItem[];
  searchItems: SearchAnswerEvidenceItem[];
  urlItems: UrlReadAnswerEvidenceItem[];
  urlCount: number;
} {
  const urlReadRefs = sourceRefs.filter(source => source.kind === 'url_read');
  const deepReadUrls = new Set(urlReadRefs.map(source => normalizeUrlKey(source.url)).filter(Boolean));
  const searchUrls = new Set(
    sourceRefs
      .filter(source => source.kind === 'search')
      .map(source => normalizeUrlKey(source.url))
      .filter(Boolean),
  );
  const searchItems: SearchAnswerEvidenceItem[] = [];
  const urlItems: UrlReadAnswerEvidenceItem[] = [];

  const canonicalRefs = dedupeSourceReferences(sourceRefs);
  const orderedRefs = canonicalRefs.some(source => source.citation_index != null)
    ? canonicalRefs
        .map((source, stableIndex) => ({ source, stableIndex }))
        .sort((left, right) => {
          const leftCitation = left.source.citation_index;
          const rightCitation = right.source.citation_index;
          if (leftCitation != null && rightCitation != null) {
            return leftCitation - rightCitation || left.stableIndex - right.stableIndex;
          }
          if (leftCitation != null) return -1;
          if (rightCitation != null) return 1;
          return left.stableIndex - right.stableIndex;
        })
        .map(({ source }) => source)
    : [
        ...canonicalRefs.filter(source => source.kind === 'search'),
        ...canonicalRefs.filter(source => source.kind === 'url_read'),
      ];

  orderedRefs.forEach((source, index) => {
    const base = {
      id: source.evidence_id ? `source-ref-${source.evidence_id}` : `source-ref-${index}`,
      evidenceId: source.evidence_id,
      citationIndex: source.citation_index,
      title: normalizeTitle(source.title, source.url),
      url: source.url,
      domain: normalizeDomain(source.domain, source.url),
      favicon: normalizeFavicon(source.url, source.favicon || findFallbackFavicon(source.url, faviconFallbacks)),
    };

    if (source.kind === 'search') {
      const urlKey = normalizeUrlKey(source.url);
      searchItems.push({
        ...base,
        kind: 'search_source',
        sourceIndex: index,
        deepRead: deepReadUrls.has(urlKey),
      });
    } else {
      const urlKey = normalizeUrlKey(source.url);
      if (searchUrls.has(urlKey)) {
        return;
      }
      urlItems.push({
        ...base,
        kind: 'url_read',
      });
    }
  });

  return {
    items: sortEvidenceItemsByCitation([...searchItems, ...urlItems]),
    searchItems,
    urlItems,
    urlCount: deepReadUrls.size,
  };
}

function dedupeSourceReferences(sourceRefs: SourceReference[]): SourceReference[] {
  const result: SourceReference[] = [];
  const indexByKey = new Map<string, number>();

  sourceRefs.forEach((source, sourceIndex) => {
    const key = source.evidence_id || normalizeUrlKey(source.url) || `source-${sourceIndex}`;
    const existingIndex = indexByKey.get(key);
    if (existingIndex == null) {
      indexByKey.set(key, result.length);
      result.push(source);
      return;
    }

    const existing = result[existingIndex];
    const preferSearch = existing.kind === 'url_read' && source.kind === 'search';
    const preferred = preferSearch ? source : existing;
    const fallback = preferSearch ? existing : source;
    result[existingIndex] = {
      ...fallback,
      ...preferred,
      evidence_id: preferred.evidence_id ?? fallback.evidence_id,
      citation_index: preferred.citation_index ?? fallback.citation_index,
      favicon: preferred.favicon ?? fallback.favicon,
      domain: preferred.domain ?? fallback.domain,
    };
  });

  return result;
}

function toLegacyEvidenceItems(
  searchSources: SearchSourceSummary[],
  urlBlocks: UrlBlock[],
): {
  searchItems: SearchAnswerEvidenceItem[];
  urlItems: UrlReadAnswerEvidenceItem[];
  urlCount: number;
} {
  const successfulUrlBlocks = urlBlocks.filter(isSuccessfulUrlBlock);
  const deepReadUrls = new Set(successfulUrlBlocks.map(block => normalizeUrlKey(block.url)).filter(Boolean));
  const searchUrls = new Set(searchSources.map(source => normalizeUrlKey(source.url)).filter(Boolean));
  return {
    searchItems: searchSources.map((source, index) => toSearchEvidenceItem(source, index, deepReadUrls)),
    urlItems: successfulUrlBlocks
      .filter(block => !searchUrls.has(normalizeUrlKey(block.url)))
      .map(toUrlEvidenceItem),
    urlCount: deepReadUrls.size,
  };
}

interface FaviconFallbacks {
  byUrl: Map<string, string>;
  byDomain: Map<string, string>;
}

function buildFaviconFallbacks(
  searchSources: SearchSourceSummary[],
  urlBlocks: UrlBlock[],
): FaviconFallbacks {
  const byUrl = new Map<string, string>();
  const byDomain = new Map<string, string>();

  const add = (url: string, favicon?: string) => {
    if (!favicon?.trim()) return;
    byUrl.set(url, favicon);
    const domain = deriveDomain(url);
    if (!byDomain.has(domain)) {
      byDomain.set(domain, favicon);
    }
  };

  searchSources.forEach(source => add(source.url, source.favicon));
  urlBlocks.forEach(block => add(block.url, block.favicon));

  return { byUrl, byDomain };
}

function findFallbackFavicon(url: string, fallback: FaviconFallbacks): string | undefined {
  return fallback.byUrl.get(url) ?? fallback.byDomain.get(deriveDomain(url));
}

function normalizeFavicon(url: string, favicon: string | undefined): string | undefined {
  const trimmed = favicon?.trim();
  if (trimmed) {
    return trimmed;
  }

  return deriveSameOriginFavicon(url);
}

function deriveSameOriginFavicon(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}/favicon.ico`;
  } catch {
    return undefined;
  }
}

function isUsableSourceRef(source: SourceReference): boolean {
  if (!source.url?.trim()) {
    return false;
  }

  return source.status == null || source.status === 'success';
}

function isSuccessfulUrlBlock(block: UrlBlock): boolean {
  return block.status == null || block.status === 'success';
}

function normalizeTitle(title: string | undefined, fallbackUrl: string): string {
  return title?.trim() || fallbackUrl;
}

function normalizeDomain(domain: string | undefined, fallbackUrl: string): string {
  return domain?.trim() || deriveDomain(fallbackUrl);
}

function deriveDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, '') || url;
  } catch {
    return url;
  }
}

function buildSummary(searchCount: number, urlCount: number, searchProviderLabel?: string): string {
  const parts = ['回答依据'];

  if (searchCount > 0) {
    parts.push(`搜索候选 ${searchCount} 条`);
  }

  if (urlCount > 0) {
    parts.push(`深读 ${urlCount} 个网页`);
  }

  if (searchCount > 0 && searchProviderLabel) {
    parts.push(`本次搜索由 ${searchProviderLabel} 提供`);
  }

  return parts.join(' · ');
}

function buildAgentEvidenceSummary({
  usedCount,
  candidateCount,
  urlCount,
  searchProviderLabel,
}: {
  usedCount: number;
  candidateCount: number;
  urlCount: number;
  searchProviderLabel?: string;
}): string {
  const parts = ['回答依据'];

  if (usedCount > 0) {
    parts.push(`已使用 ${usedCount} 条`);
  }

  if (candidateCount > 0) {
    parts.push(`候选 ${candidateCount} 条`);
  }

  if (urlCount > 0) {
    parts.push(`深读 ${urlCount} 个网页`);
  }

  if ((usedCount > 0 || candidateCount > 0) && searchProviderLabel) {
    parts.push(`本次搜索由 ${searchProviderLabel} 提供`);
  }

  return parts.join(' · ');
}

function normalizeUrlKey(url: string | undefined): string {
  const trimmed = url?.trim();
  if (!trimmed) {
    return '';
  }

  try {
    const parsed = new URL(trimmed);
    parsed.hash = '';
    parsed.hostname = parsed.hostname.toLowerCase();
    parsed.pathname = parsed.pathname.replace(/\/+$/, '');
    return parsed.toString();
  } catch {
    return trimmed.replace(/\/+$/, '');
  }
}

const SEARCH_PROVIDER_LABELS: Record<string, string> = {
  brave: 'Brave',
  firecrawl: 'Firecrawl',
  tavily: 'Tavily',
};

function deriveSearchProviderLabel(provider: string | null | undefined): string | undefined {
  if (!provider) {
    return undefined;
  }

  const trimmed = provider.trim();
  if (!trimmed) {
    return undefined;
  }

  const normalized = trimmed.toLowerCase();
  return SEARCH_PROVIDER_LABELS[normalized] ?? trimmed;
}
