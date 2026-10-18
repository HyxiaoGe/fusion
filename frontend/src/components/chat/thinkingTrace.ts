import { getToolMeta } from '@/lib/agent/toolRegistry';
import type { AgentRunState } from '@/types/agentRun';
import type { ContentBlock } from '@/types/conversation';

/** 深度思考面板里按发生顺序排列的一项：一轮思考，或这轮思考之后调用的工具。 */
export type ThinkingTraceEntry =
  | { kind: 'thinking'; id: string; text: string }
  | { kind: 'tool'; id: string; toolName: string; summary: string };

/**
 * 把多轮思考和工具调用按真实顺序穿插起来。
 *
 * 生成中流槽位把工具结果、思考、正文分组拼接，块顺序不可信，这时按运行步骤
 * （每步先思考、再调用工具）排列；历史消息的块按服务端写入顺序保存，直接按块顺序排列。
 */
export function buildThinkingTrace(
  blocks: readonly ContentBlock[],
  run?: AgentRunState | null,
): ThinkingTraceEntry[] {
  const thinkingById = new Map<string, string>();
  for (const block of blocks) {
    if (block.type === 'thinking' && block.thinking.trim()) thinkingById.set(block.id, block.thinking);
  }
  if (thinkingById.size === 0) return [];

  const fromRun = run ? traceFromRunSteps(run, thinkingById) : null;
  return dropLeadingTools(fromRun ?? traceFromBlockOrder(blocks));
}

export function countThinkingRounds(trace: readonly ThinkingTraceEntry[]): number {
  return trace.filter(entry => entry.kind === 'thinking').length;
}

function traceFromRunSteps(
  run: AgentRunState,
  thinkingById: ReadonlyMap<string, string>,
): ThinkingTraceEntry[] | null {
  if (run.steps.length === 0) return null;
  const covered = new Set(run.steps.flatMap(step => step.contentBlockIds));
  // 有思考没挂到步骤上（旧协议或晚到事件）时步骤顺序不完整，退回块顺序。
  for (const id of thinkingById.keys()) {
    if (!covered.has(id)) return null;
  }

  const trace: ThinkingTraceEntry[] = [];
  for (const step of run.steps) {
    for (const blockId of step.contentBlockIds) {
      const text = thinkingById.get(blockId);
      if (text) trace.push({ kind: 'thinking', id: blockId, text });
    }
    for (const call of step.toolCalls) {
      trace.push({ kind: 'tool', id: call.toolCallId, toolName: call.toolName, summary: summarize(call.toolName, call.arguments) });
    }
  }
  return trace;
}

function traceFromBlockOrder(blocks: readonly ContentBlock[]): ThinkingTraceEntry[] {
  const trace: ThinkingTraceEntry[] = [];
  for (const block of blocks) {
    if (block.type === 'thinking') {
      if (block.thinking.trim()) trace.push({ kind: 'thinking', id: block.id, text: block.thinking });
      continue;
    }
    const tool = toolCallFromResultBlock(block);
    if (!tool) continue;
    const summary = block.type === 'data_source' ? block.label : summarize(tool.toolName, tool.args);
    trace.push({ kind: 'tool', id: block.id, toolName: tool.toolName, summary });
  }
  return trace;
}

/** 历史消息只存工具结果块，按块类型还原成工具名和摘要参数，复用工具注册表的文案。 */
function toolCallFromResultBlock(
  block: ContentBlock,
): { toolName: string; args: Record<string, unknown> } | null {
  switch (block.type) {
    case 'search':
      return { toolName: 'web_search', args: { query: block.query } };
    case 'url_read':
      return { toolName: 'url_read', args: { url: block.title || block.url } };
    case 'knowledge_evidence':
      return { toolName: 'knowledge_search', args: { query: block.query } };
    case 'image_results':
      return { toolName: 'image_search', args: { query: block.query } };
    case 'video_results':
      return { toolName: 'video_search', args: { query: block.query } };
    case 'generated_image':
      return { toolName: 'generate_image', args: { prompt: block.prompt } };
    case 'document':
      return {
        toolName: block.operation === 'edited' ? 'edit_document' : 'create_document',
        args: { title: block.title },
      };
    case 'place_results':
      return { toolName: 'local_place_search', args: { query: block.query ?? '', near: block.near ?? '' } };
    case 'route_results':
      return {
        toolName: 'route_compare',
        args: { origin: block.origin?.label ?? '', destination: block.destination?.label ?? '' },
      };
    case 'flight_results':
      return { toolName: 'search_flights', args: { origin: block.origin ?? '', destination: block.destination ?? '' } };
    case 'train_results':
      return { toolName: 'search_trains', args: { origin: block.origin ?? '', destination: block.destination ?? '' } };
    case 'weather_results':
      return { toolName: 'weather_forecast', args: { location: block.query } };
    case 'itinerary_results':
      return { toolName: 'present_itinerary', args: { title: 'title' in block ? block.title : '' } };
    case 'data_source':
      return { toolName: block.provider, args: {} };
    default:
      return null;
  }
}

function summarize(toolName: string, args: Record<string, unknown>): string {
  return getToolMeta(toolName).summarize(args ?? {});
}

function dropLeadingTools(trace: ThinkingTraceEntry[]): ThinkingTraceEntry[] {
  const first = trace.findIndex(entry => entry.kind === 'thinking');
  return first <= 0 ? trace : trace.slice(first);
}
