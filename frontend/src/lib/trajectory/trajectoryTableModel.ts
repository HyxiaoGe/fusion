import { extractTextFromBlocks } from '@/types/conversation';
import type { TrajectoryCell } from './TrajectoryCellProjection';
import {
  getTrajectoryCellPresentation,
  toolArgumentSummary,
  trajectoryToolLabel,
} from './trajectoryCellPresentation';

export interface TrajectoryTableRow {
  key: string;
  cell: TrajectoryCell;
  sourceIndex: number;
  /** 表格行号：按收起单次成功尝试后的行顺序连续编号，搜索与聚焦过滤不改变行号。 */
  rowNumber: number;
  turnNumber: number | null;
  /** 后端提供的 1 基尝试序号；未知时保持 null。 */
  attemptNumber: number | null;
  kindLabel: string;
  summary: string;
  statusLabel: string | null;
  trajectoryStatusLabel: string | null;
  durationMs: number | null;
  searchText: string;
  matched: boolean;
  matchPending: boolean;
  matchFieldLabel: string | null;
  matchExcerpt: string | null;
  attemptCount: number;
  aliasedCellKeys: string[];
  /** 缩进层级：工具挂在发起它的模型轮次下，尝试再挂在工具下。 */
  depth: number;
}

interface TrajectoryTableModelInput {
  cells: readonly TrajectoryCell[];
  searchQuery?: string;
  focusedCellKeys?: ReadonlySet<string> | null;
}

interface MutableTableRow extends TrajectoryTableRow {
  directSearchMatch: boolean;
  directFocusMatch: boolean;
}

const KIND_LABELS: Record<TrajectoryCell['type'], string> = {
  user: '用户',
  message: '消息',
  assistant_request: '模型',
  run: '运行',
  plan: '计划',
  context: '上下文',
  tool: '工具',
  subtool: '尝试',
  compacted: '压缩',
  model_fallback: '换模型',
};

const COLLAPSIBLE_ATTEMPT_STATUSES = new Set(['success', 'completed', 'complete']);
// 成功是常态，状态列只标出运行中、失败、降级等需要留意的情况。
const SILENT_STATUS_TONES = new Set(['success']);
// 每轮都有的「上下文充足」不携带新信息（输入 Token 已在模型请求行），表格里不单独成行。
const NO_OP_CONTEXT_STATUSES = new Set(['no_op', 'no_op_fast_path']);

/** 将 cell 投影为不依赖 React/Redux/fetch 的稳定记录表行。 */
export function projectTrajectoryTableRows({
  cells,
  searchQuery = '',
  focusedCellKeys = null,
}: TrajectoryTableModelInput): TrajectoryTableRow[] {
  const normalizedQuery = normalizeSearch(searchQuery);
  const hasFocus = focusedCellKeys !== null;
  const metadata = buildRowMetadata(cells);
  const attemptsByTool = collectAttemptsByTool(cells);
  const collapsedAttemptKeys = new Set<string>();
  const collapsedKeysByTool = new Map<string, string[]>();

  for (const [toolIdentity, attempts] of attemptsByTool) {
    if (attempts.length !== 1 || !COLLAPSIBLE_ATTEMPT_STATUSES.has(attempts[0].status)) continue;
    collapsedAttemptKeys.add(attempts[0].key);
    collapsedKeysByTool.set(toolIdentity, [attempts[0].key]);
  }

  const rows: MutableTableRow[] = [];
  const runRowsById = new Map<string, MutableTableRow>();
  const userRowsById = new Map<string, MutableTableRow>();
  const userRowsByTurn = new Map<number, MutableTableRow>();
  const toolRowsByIdentity = new Map<string, MutableTableRow>();

  for (let sourceIndex = 0; sourceIndex < cells.length; sourceIndex += 1) {
    const cell = cells[sourceIndex];
    if (collapsedAttemptKeys.has(cell.key) || isNoOpContextStatus(cell)) continue;
    const presentation = getTrajectoryCellPresentation(cell);
    const kindLabel = KIND_LABELS[cell.type];
    const summary = tableSummary(cell, presentation.summary);
    const statusLabel = SILENT_STATUS_TONES.has(presentation.tone) ? null : presentation.statusLabel;
    const aliasedCellKeys = cell.type === 'tool'
      ? (collapsedKeysByTool.get(toolIdentity(cell.runId, cell.toolCallId)) ?? [])
      : [];
    const attemptCount = cell.type === 'tool'
      ? (attemptsByTool.get(toolIdentity(cell.runId, cell.toolCallId))?.length ?? 0)
      : 0;
    const messageText = searchableMessageText(cell);
    const searchText = normalizeSearch([
      cell.type,
      kindLabel,
      summary,
      cell.type === 'tool' || cell.type === 'subtool' ? cell.toolName : null,
      statusLabel,
      presentation.trajectoryStatusLabel,
      messageText,
    ].filter(Boolean).join(' '));
    const collapsedFocusMatch = cell.type === 'tool'
      && aliasedCellKeys.some(key => focusedCellKeys?.has(key));
    const directSearchMatch = normalizedQuery ? searchText.includes(normalizedQuery) : true;
    const directFocusMatch = hasFocus
      ? Boolean(focusedCellKeys?.has(cell.key) || collapsedFocusMatch)
      : true;
    const matchPending = Boolean(normalizedQuery)
      && directFocusMatch
      && cell.type === 'run'
      && !cell.isHydrated
      && !directSearchMatch;
    const matchExcerpt = normalizedQuery && directSearchMatch
      ? messageMatchExcerpt(messageText, summary, normalizedQuery)
      : null;
    const row: MutableTableRow = {
      key: cell.key,
      cell,
      sourceIndex,
      rowNumber: rows.length + 1,
      turnNumber: metadata[sourceIndex].turnNumber,
      attemptNumber: cell.type === 'run' || cell.type === 'subtool'
        ? cell.attemptIndex
        : null,
      kindLabel,
      summary,
      statusLabel,
      trajectoryStatusLabel: presentation.trajectoryStatusLabel ?? null,
      durationMs: presentation.durationMs,
      searchText,
      matched: Boolean(normalizedQuery) && directSearchMatch && directFocusMatch,
      matchPending,
      matchFieldLabel: matchExcerpt ? '消息正文' : null,
      matchExcerpt,
      attemptCount,
      aliasedCellKeys,
      depth: rowDepth(cell),
      directSearchMatch,
      directFocusMatch,
    };
    rows.push(row);
    if (cell.type === 'run') runRowsById.set(cell.runId, row);
    if (cell.type === 'user') {
      userRowsById.set(cell.userMessageId, row);
      if (row.turnNumber !== null) userRowsByTurn.set(row.turnNumber, row);
    }
    if (cell.type === 'tool') {
      toolRowsByIdentity.set(toolIdentity(cell.runId, cell.toolCallId), row);
    }
  }

  if (!normalizedQuery && !hasFocus) return stripPrivateFields(rows);
  if (!hasFocus) {
    return stripPrivateFields(rows.filter(row => row.directSearchMatch));
  }

  const selectedKeys = new Set<string>();
  const roots = rows.filter(row => (
    row.directFocusMatch && (row.directSearchMatch || row.matchPending)
  ));
  for (const row of roots) {
    selectedKeys.add(row.key);
    const runRow = row.cell.runId ? runRowsById.get(row.cell.runId) : undefined;
    if (runRow) selectedKeys.add(runRow.key);
    const userMessageId = row.cell.userMessageId ?? runRow?.cell.userMessageId ?? null;
    const userRow = userMessageId
      ? userRowsById.get(userMessageId)
      : (row.turnNumber === null ? undefined : userRowsByTurn.get(row.turnNumber));
    if (userRow) selectedKeys.add(userRow.key);
    if (row.cell.type === 'subtool' && row.cell.toolCallId) {
      const toolRow = toolRowsByIdentity.get(toolIdentity(row.cell.runId, row.cell.toolCallId));
      if (toolRow) selectedKeys.add(toolRow.key);
    }
  }

  return stripPrivateFields(rows.filter(row => selectedKeys.has(row.key)));
}

function buildRowMetadata(cells: readonly TrajectoryCell[]): Array<{ turnNumber: number | null }> {
  let turnNumber = 0;
  let currentTurnNumber: number | null = null;
  const turnsByUserMessageId = new Map<string, number>();
  const turnsByRunId = new Map<string, number | null>();
  return cells.map(cell => {
    if (cell.type === 'user') {
      turnNumber += 1;
      currentTurnNumber = turnNumber;
      turnsByUserMessageId.set(cell.userMessageId, turnNumber);
      return { turnNumber };
    }
    if (cell.type === 'run') {
      const runTurn = cell.association === 'unassociated'
        ? null
        : (cell.userMessageId
          ? turnsByUserMessageId.get(cell.userMessageId) ?? currentTurnNumber
          : currentTurnNumber);
      turnsByRunId.set(cell.runId, runTurn);
      return { turnNumber: runTurn };
    }
    if (cell.runId !== null) {
      return { turnNumber: turnsByRunId.get(cell.runId) ?? null };
    }
    return { turnNumber: currentTurnNumber };
  });
}

function collectAttemptsByTool(
  cells: readonly TrajectoryCell[],
): Map<string, Array<Extract<TrajectoryCell, { type: 'subtool' }>>> {
  const tools = new Set(cells
    .filter((cell): cell is Extract<TrajectoryCell, { type: 'tool' }> => cell.type === 'tool')
    .map(cell => toolIdentity(cell.runId, cell.toolCallId)));
  const attempts = new Map<string, Array<Extract<TrajectoryCell, { type: 'subtool' }>>>();
  for (const cell of cells) {
    if (cell.type !== 'subtool' || cell.toolCallId === null) continue;
    const identity = toolIdentity(cell.runId, cell.toolCallId);
    if (!tools.has(identity)) continue;
    const grouped = attempts.get(identity) ?? [];
    grouped.push(cell);
    attempts.set(identity, grouped);
  }
  return attempts;
}

function isNoOpContextStatus(cell: TrajectoryCell): boolean {
  return cell.type === 'context'
    && cell.eventType === 'context_status_updated'
    && NO_OP_CONTEXT_STATUSES.has(String(cell.payload.status ?? ''));
}

function toolIdentity(runId: string, toolCallId: string): string {
  return `${runId}\u0000${toolCallId}`;
}

function tableSummary(cell: TrajectoryCell, summary: string): string {
  if (cell.type === 'tool') {
    return [trajectoryToolLabel(cell.toolName), toolArgumentSummary(cell)].filter(Boolean).join(' · ');
  }
  return summary;
}

function rowDepth(cell: TrajectoryCell): number {
  if (cell.type === 'tool' || cell.type === 'plan') return cell.llmRoundKey ? 1 : 0;
  if (cell.type === 'subtool') return cell.llmRoundKey ? 2 : 1;
  return 0;
}

function searchableMessageText(cell: TrajectoryCell): string {
  if (cell.type === 'user' || cell.type === 'message') {
    return extractTextFromBlocks(cell.message.content).trim().replace(/\s+/g, ' ');
  }
  return '';
}

function messageMatchExcerpt(
  messageText: string,
  summary: string,
  normalizedQuery: string,
): string | null {
  if (!messageText || normalizeSearch(summary).includes(normalizedQuery)) return null;
  const normalizedMessage = messageText.toLocaleLowerCase();
  const matchIndex = normalizedMessage.indexOf(normalizedQuery);
  if (matchIndex < 0) return null;
  const contextLength = 32;
  const start = Math.max(0, matchIndex - contextLength);
  const end = Math.min(messageText.length, matchIndex + normalizedQuery.length + contextLength);
  return `${start > 0 ? '…' : ''}${messageText.slice(start, end)}${end < messageText.length ? '…' : ''}`;
}

function normalizeSearch(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function stripPrivateFields(rows: MutableTableRow[]): TrajectoryTableRow[] {
  return rows.map(row => ({
    key: row.key,
    cell: row.cell,
    sourceIndex: row.sourceIndex,
    rowNumber: row.rowNumber,
    turnNumber: row.turnNumber,
    attemptNumber: row.attemptNumber,
    kindLabel: row.kindLabel,
    summary: row.summary,
    statusLabel: row.statusLabel,
    trajectoryStatusLabel: row.trajectoryStatusLabel,
    durationMs: row.durationMs,
    searchText: row.searchText,
    matched: row.matched,
    matchPending: row.matchPending,
    matchFieldLabel: row.matchFieldLabel,
    matchExcerpt: row.matchExcerpt,
    attemptCount: row.attemptCount,
    aliasedCellKeys: row.aliasedCellKeys,
    depth: row.depth,
  }));
}
