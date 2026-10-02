import { compareDocumentTokens, type DocumentTextDiffSegment } from './documentDiff';

export interface DocumentHighlightRange {
  start: number;
  end: number;
  kind: 'added' | 'removed' | 'modified';
}

export interface DocumentHighlights {
  before: DocumentHighlightRange[];
  after: DocumentHighlightRange[];
}

interface TableCell {
  start: number;
  end: number;
  text: string;
}

interface DocumentLine {
  text: string;
  content: string;
  start: number;
  cells?: TableCell[];
  tableRole?: 'header' | 'delimiter' | 'body';
}

const LINE_COMPARISON_BUDGET = 1_000_000;
const INLINE_COMPARISON_BUDGET = 200_000;
const INLINE_TEXT_LIMIT = 2_000;
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;

/** 范围使用输入原文的 UTF-16 偏移；若调用方统一 CRLF，必须以同一份文字计算和渲染。 */
export function buildDocumentHighlights(before: string, after: string): DocumentHighlights {
  const result: DocumentHighlights = { before: [], after: [] };
  if (before === after) return result;
  const beforeLines = documentLines(before);
  const afterLines = documentLines(after);
  const operations = compareDocumentTokens(beforeLines.map(line => line.text), afterLines.map(line => line.text), LINE_COMPARISON_BUDGET);
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('zh', { granularity: 'grapheme' }) : null;
  let inlineBudget = INLINE_COMPARISON_BUDGET;

  const append = (side: keyof DocumentHighlights, start: number, end: number, kind: DocumentHighlightRange['kind']) => {
    if (end <= start) return;
    const ranges = result[side];
    const previous = ranges.at(-1);
    if (previous?.kind === kind && previous.end === start) previous.end = end;
    else ranges.push({ start, end, kind });
  };
  const markLine = (line: DocumentLine, side: keyof DocumentHighlights, kind: 'added' | 'removed') => {
    if (line.tableRole !== 'delimiter') append(side, line.start, line.start + line.content.length, kind);
  };
  const markTextChange = (left: DocumentLine, right: DocumentLine) => {
    if (left.content === right.content) return;
    if (left.cells && right.cells && left.tableRole === right.tableRole) {
      if (left.tableRole === 'delimiter') return;
      // 表格按完整单元格定位，保留未变单元格、竖线和边界空格。
      const count = Math.max(left.cells.length, right.cells.length);
      for (let index = 0; index < count; index += 1) {
        const oldCell = left.cells[index];
        const newCell = right.cells[index];
        if (oldCell?.text === newCell?.text) continue;
        if (oldCell) append('before', oldCell.start, oldCell.end, 'modified');
        if (newCell) append('after', newCell.start, newCell.end, 'modified');
      }
      return;
    }
    if (!left.content || !right.content) {
      markLine(left, 'before', 'removed');
      markLine(right, 'after', 'added');
      return;
    }

    if (left.content.length + right.content.length > INLINE_TEXT_LIMIT || inlineBudget <= 0) {
      const [leftRange, rightRange] = changedMiddle(left.content, right.content, segmenter);
      append('before', left.start + leftRange[0], left.start + leftRange[1], 'modified');
      append('after', right.start + rightRange[0], right.start + rightRange[1], 'modified');
      return;
    }
    const tokenize = (text: string) => segmenter
      ? Array.from(segmenter.segment(text), item => item.segment)
      : fallbackGraphemes(text);
    const leftTokens = tokenize(left.content);
    const rightTokens = tokenize(right.content);
    let prefix = 0;
    while (prefix < leftTokens.length && prefix < rightTokens.length && leftTokens[prefix] === rightTokens[prefix]) prefix += 1;
    let suffix = 0;
    while (suffix < leftTokens.length - prefix && suffix < rightTokens.length - prefix
      && leftTokens[leftTokens.length - suffix - 1] === rightTokens[rightTokens.length - suffix - 1]) suffix += 1;
    const cost = (leftTokens.length - prefix - suffix) * (rightTokens.length - prefix - suffix);
    const textOperations = compareDocumentTokens(leftTokens, rightTokens, inlineBudget);
    inlineBudget = Math.max(0, inlineBudget - cost);
    let leftOffset = left.start;
    let rightOffset = right.start;
    for (const operation of textOperations) {
      if (operation.kind === 'equal') {
        leftOffset += operation.text.length;
        rightOffset += operation.text.length;
      } else if (operation.kind === 'removed') {
        append('before', leftOffset, leftOffset + operation.text.length, 'modified');
        leftOffset += operation.text.length;
      } else {
        append('after', rightOffset, rightOffset + operation.text.length, 'modified');
        rightOffset += operation.text.length;
      }
    }
  };
  const markPairs = (left: DocumentLine[], right: DocumentLine[]) => {
    const count = Math.max(left.length, right.length);
    for (let index = 0; index < count; index += 1) {
      const oldLine = left[index];
      const newLine = right[index];
      if (oldLine && newLine && oldLine.tableRole === newLine.tableRole) markTextChange(oldLine, newLine);
      else {
        if (oldLine) markLine(oldLine, 'before', 'removed');
        if (newLine) markLine(newLine, 'after', 'added');
      }
    }
  };
  const markChanges = (left: DocumentLine[], right: DocumentLine[]) => {
    if (left.length && right.length && left.every(line => line.cells) && right.every(line => line.cells)) {
      // 行插入与修改处于同一段时，先用首列对齐，避免后续整行被错配。
      const key = (line: DocumentLine) => `${JSON.stringify([line.tableRole, line.cells?.[0]?.text])}\n`;
      const keyed = compareDocumentTokens(left.map(key), right.map(key), LINE_COMPARISON_BUDGET);
      walkChanges(left, right, keyed, operation => operation.text.split('\n').length - 1, markPairs, markTextChange);
    } else markPairs(left, right);
  };

  walkChanges(beforeLines, afterLines, operations, operation => lineCount(operation.text), markChanges);
  return result;
}

/** 将 LCS 操作还原成原文行，未变行无需生成范围。 */
function walkChanges(
  before: DocumentLine[],
  after: DocumentLine[],
  operations: DocumentTextDiffSegment[],
  countTokens: (operation: DocumentTextDiffSegment) => number,
  onChange: (before: DocumentLine[], after: DocumentLine[]) => void,
  onEqual?: (before: DocumentLine, after: DocumentLine) => void,
) {
  let leftIndex = 0;
  let rightIndex = 0;
  let removed: DocumentLine[] = [];
  let added: DocumentLine[] = [];
  const flush = () => {
    if (removed.length || added.length) onChange(removed, added);
    removed = [];
    added = [];
  };
  for (const operation of operations) {
    const count = countTokens(operation);
    if (operation.kind === 'equal') {
      flush();
      if (onEqual) for (let index = 0; index < count; index += 1) onEqual(before[leftIndex + index], after[rightIndex + index]);
      leftIndex += count;
      rightIndex += count;
    } else if (operation.kind === 'removed') {
      removed = removed.concat(before.slice(leftIndex, leftIndex + count));
      leftIndex += count;
    } else {
      added = added.concat(after.slice(rightIndex, rightIndex + count));
      rightIndex += count;
    }
  }
  flush();
}

function lineCount(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) if (text[index] === '\n') count += 1;
  return count + (text.endsWith('\n') ? 0 : 1);
}

function documentLines(text: string): DocumentLine[] {
  let offset = 0;
  const lines = (text.match(/[^\n]*(?:\n|$)/g) ?? []).filter(Boolean).map(raw => {
    const line = { text: raw, content: raw.replace(/\r?\n$/, ''), start: offset };
    offset += raw.length;
    return line as DocumentLine;
  });
  let fence: string | null = null;
  for (let index = 0; index < lines.length; index += 1) {
    const opening = FENCE_OPEN.exec(lines[index].content);
    if (fence) {
      if (new RegExp(`^ {0,3}${fence[0]}{${fence.length},}\\s*$`).test(lines[index].content)) fence = null;
      continue;
    }
    if (opening) {
      fence = opening[1];
      continue;
    }
    const header = tableCells(lines[index]);
    const delimiter = index + 1 < lines.length ? tableCells(lines[index + 1]) : null;
    if (!header || !delimiter || header.length !== delimiter.length || !delimiter.every(cell => /^:?-+:?$/.test(cell.text))) continue;
    lines[index].cells = header;
    lines[index].tableRole = 'header';
    lines[index + 1].cells = delimiter;
    lines[index + 1].tableRole = 'delimiter';
    index += 2;
    while (index < lines.length) {
      const cells = tableCells(lines[index]);
      if (!cells) break;
      lines[index].cells = cells;
      lines[index].tableRole = 'body';
      index += 1;
    }
    index -= 1;
  }
  return lines;
}

function tableCells(line: DocumentLine): TableCell[] | null {
  const pipes: number[] = [];
  let escaped = false;
  for (let index = 0; index < line.content.length; index += 1) {
    const character = line.content[index];
    if (character === '|' && !escaped) pipes.push(index);
    escaped = character === '\\' ? !escaped : false;
  }
  if (!pipes.length || /^ {4}|^\t/.test(line.content)) return null;
  const boundaries = [-1, ...pipes, line.content.length];
  if (!line.content.slice(0, pipes[0]).trim()) boundaries.shift();
  if (!line.content.slice(pipes[pipes.length - 1] + 1).trim()) boundaries.pop();
  const cells: TableCell[] = [];
  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const start = boundaries[index] + 1;
    const raw = line.content.slice(start, boundaries[index + 1]);
    const trimmed = raw.trim();
    const trimmedStart = start + raw.length - raw.trimStart().length;
    cells.push({ start: line.start + trimmedStart, end: line.start + trimmedStart + trimmed.length, text: trimmed });
  }
  return cells.length ? cells : null;
}

function changedMiddle(before: string, after: string, segmenter: Intl.Segmenter | null): [[number, number], [number, number]] {
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < before.length - prefix && suffix < after.length - prefix
    && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix += 1;
  const range = (text: string): [number, number] => {
    let start = prefix;
    let end = text.length - suffix;
    if (segmenter) {
      const segments = segmenter.segment(text);
      start = segments.containing(start)?.index ?? start;
      const last = end > 0 ? segments.containing(end - 1) : undefined;
      if (last) end = last.index + last.segment.length;
    } else {
      let offset = 0;
      for (const token of fallbackGraphemes(text)) {
        const next = offset + token.length;
        if (offset <= start && start < next) start = offset;
        if (offset < end && end <= next) end = next;
        offset = next;
      }
    }
    return [start, end];
  };
  const left = range(before);
  const right = range(after);
  const safePrefix = Math.min(left[0], right[0]);
  const safeSuffix = Math.min(before.length - left[1], after.length - right[1]);
  return [[safePrefix, before.length - safeSuffix], [safePrefix, after.length - safeSuffix]];
}

/** 老环境保留代理对、组合符、肤色与 ZWJ 序列，现代环境使用标准字素分段器。 */
function fallbackGraphemes(text: string): string[] {
  const tokens: string[] = [];
  let regionalCount = 0;
  for (const character of text) {
    const previous = tokens.at(-1);
    const regional = /\p{Regional_Indicator}/u.test(character);
    const joins = previous && (/\p{Grapheme_Extend}|\p{Spacing_Mark}|\p{Emoji_Modifier}|\u200d/u.test(character)
      || previous.endsWith('\u200d') || (regional && regionalCount % 2 === 1));
    if (joins) tokens[tokens.length - 1] += character;
    else tokens.push(character);
    regionalCount = regional ? regionalCount + 1 : 0;
  }
  return tokens;
}
