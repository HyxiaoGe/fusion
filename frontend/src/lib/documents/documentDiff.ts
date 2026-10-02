export interface DocumentDiffSnapshot {
  title: string;
  content: string;
}

export type DocumentDiffSegment =
  | { kind: 'equal'; text: string }
  | { kind: 'change'; scope: 'title' | 'body'; before: string; after: string; index: number };

export interface DocumentDiffResult {
  segments: DocumentDiffSegment[];
  changeCount: number;
}

export interface DocumentTextDiffSegment {
  text: string;
  kind: 'equal' | 'added' | 'removed';
}

const BLOCK_COMPARISON_BUDGET = 1_000_000;
const TEXT_COMPARISON_BUDGET = 120_000;
const INLINE_TEXT_LIMIT = 2_000;
const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const CONTAINER_OPEN = /^ {0,3}(:{3,})[a-z]+(?:\[[^\]\r\n]*\])?\s*$/;
const CONTAINER_CLOSE = /^ {0,3}(:{3,})\s*$/;
const LIST_START = /^ {0,3}(?:[-+*]|\d+[.)])\s+\S/;
const HEADING = /^ {0,3}#{1,6}(?:\s|$)/;
const RULE = /^ {0,3}(?:[-*_]\s*){3,}$/;
const SETEXT_HEADING = /^ {0,3}(?:=+|-+)\s*$/;

/** 根据两个完整快照计算只读差异；空白与换行也参与比较，不修改原文。 */
export function buildDocumentDiff(before: DocumentDiffSnapshot, after: DocumentDiffSnapshot): DocumentDiffResult {
  const segments: DocumentDiffSegment[] = [];
  let changeCount = 0;
  if (before.title !== after.title) {
    segments.push({ kind: 'change', scope: 'title', before: before.title, after: after.title, index: changeCount++ });
  }

  const operations = compareTokens(splitMarkdownBlocks(before.content), splitMarkdownBlocks(after.content), BLOCK_COMPARISON_BUDGET);
  let removed = '';
  let added = '';
  const flushChange = () => {
    if (!removed && !added) return;
    segments.push({ kind: 'change', scope: 'body', before: removed, after: added, index: changeCount++ });
    removed = '';
    added = '';
  };
  for (const operation of operations) {
    if (operation.kind === 'equal') {
      flushChange();
      segments.push({ kind: 'equal', text: operation.text });
    } else if (operation.kind === 'removed') {
      removed += operation.text;
    } else {
      added += operation.text;
    }
  }
  flushChange();
  return { segments, changeCount };
}

/** 短文字按字素比较；复杂 Markdown 与长文保留整块，避免拆开语法或阻塞阅读。 */
export function diffDocumentText(before: string, after: string): DocumentTextDiffSegment[] {
  if (before === after) return before ? [{ kind: 'equal', text: before }] : [];
  if (before.length + after.length > INLINE_TEXT_LIMIT || isComplexMarkdown(before) || isComplexMarkdown(after)) {
    return wholeTextChange(before, after);
  }
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('zh', { granularity: 'grapheme' }) : null;
  const tokenize = (text: string) => segmenter
    ? Array.from(segmenter.segment(text), item => item.segment)
    : Array.from(text);
  return compareTokens(tokenize(before), tokenize(after), TEXT_COMPARISON_BUDGET);
}

function wholeTextChange(before: string, after: string): DocumentTextDiffSegment[] {
  const result: DocumentTextDiffSegment[] = [];
  if (before) result.push({ kind: 'removed', text: before });
  if (after) result.push({ kind: 'added', text: after });
  return result;
}

function isComplexMarkdown(text: string): boolean {
  return /^(?: {0,3}(?:`{3,}|~{3,}|:{3,}|>|(?:[-+*]|\d+[.)])\s)| {4}|\t)/m.test(text)
    || text.split(/\r\n|\r|\n/).some(isTableDelimiter);
}

function isTableDelimiter(line: string): boolean {
  if (!line.includes('|')) return false;
  const cells = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
  return cells.length > 0 && cells.every(cell => /^\s*:?-{3,}:?\s*$/.test(cell));
}

function splitMarkdownBlocks(text: string): string[] {
  const lines = text.match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g) ?? [];
  const plain = lines.map(line => line.replace(/(?:\r\n|\r|\n)$/, ''));
  const blocks: string[] = [];
  const blank = (index: number) => !plain[index].trim();
  const table = (index: number) => index + 1 < lines.length && plain[index].includes('|') && isTableDelimiter(plain[index + 1]);
  const special = (index: number) => FENCE_OPEN.test(plain[index]) || CONTAINER_OPEN.test(plain[index])
    || LIST_START.test(plain[index]) || HEADING.test(plain[index]) || RULE.test(plain[index])
    || /^ {0,3}>/.test(plain[index]) || /^(?: {4}|\t)\S/.test(plain[index]) || table(index);

  let index = 0;
  while (index < lines.length) {
    const start = index;
    const fence = FENCE_OPEN.exec(plain[index]);
    const container = CONTAINER_OPEN.exec(plain[index]);
    if (blank(index)) {
      while (index < lines.length && blank(index)) index += 1;
    } else if (fence) {
      index = consumeFence(plain, index, fence[1]);
    } else if (container) {
      const stack = [container[1].length];
      index += 1;
      while (index < lines.length && stack.length) {
        const nestedFence = FENCE_OPEN.exec(plain[index]);
        if (nestedFence) {
          index = consumeFence(plain, index, nestedFence[1]);
          continue;
        }
        const open = CONTAINER_OPEN.exec(plain[index]);
        const close = CONTAINER_CLOSE.exec(plain[index]);
        if (open) stack.push(open[1].length);
        if (close) {
          const matching = stack.lastIndexOf(close[1].length);
          stack.length = matching >= 0 ? matching : stack.length - 1;
        }
        index += 1;
      }
    } else if (table(index)) {
      index += 2;
      while (index < lines.length && !blank(index) && plain[index].includes('|')) index += 1;
    } else if (LIST_START.test(plain[index])) {
      index += 1;
      while (index < lines.length) {
        if (blank(index)) {
          let next = index + 1;
          while (next < lines.length && blank(next)) next += 1;
          if (next >= lines.length || !(LIST_START.test(plain[next]) || /^(?: {2,}|\t)\S/.test(plain[next]))) break;
          index = next;
        } else if (!LIST_START.test(plain[index]) && !/^(?: {2,}|\t)/.test(plain[index]) && special(index)) {
          break;
        } else {
          index += 1;
        }
      }
    } else if (HEADING.test(plain[index]) || RULE.test(plain[index])) {
      index += 1;
    } else if (/^ {0,3}>/.test(plain[index]) || /^(?: {4}|\t)\S/.test(plain[index])) {
      const pattern = /^ {0,3}>/.test(plain[index]) ? /^ {0,3}>/ : /^(?: {4}|\t)/;
      index += 1;
      while (index < lines.length && pattern.test(plain[index])) index += 1;
    } else {
      index += 1;
      while (index < lines.length && !blank(index)) {
        if (SETEXT_HEADING.test(plain[index])) {
          index += 1;
          break;
        }
        if (special(index)) break;
        index += 1;
      }
    }
    blocks.push(lines.slice(start, index).join(''));
  }
  return blocks;
}

function consumeFence(lines: string[], start: number, opening: string): number {
  const closing = new RegExp(`^ {0,3}${opening[0]}{${opening.length},}\\s*$`);
  let index = start + 1;
  while (index < lines.length) {
    if (closing.test(lines[index++])) break;
  }
  return index;
}

function compareTokens(before: string[], after: string[], budget: number): DocumentTextDiffSegment[] {
  const result: DocumentTextDiffSegment[] = [];
  const append = (kind: DocumentTextDiffSegment['kind'], text: string) => {
    if (!text) return;
    const previous = result[result.length - 1];
    if (previous?.kind === kind) previous.text += text;
    else result.push({ kind, text });
  };
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < before.length - prefix && suffix < after.length - prefix
    && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix += 1;
  append('equal', before.slice(0, prefix).join(''));
  const left = before.slice(prefix, before.length - suffix);
  const right = after.slice(prefix, after.length - suffix);

  if (!left.length || !right.length || left.length * right.length > budget) {
    // 最坏情况下只合并中段；共同前后缀仍保留，且两侧原文均能完整重建。
    append('removed', left.join(''));
    append('added', right.join(''));
  } else {
    // 字符串先映射为编号，避免 LCS 内反复比较长 Markdown 块。
    const identifiers = new Map<string, number>();
    const identify = (token: string) => {
      const known = identifiers.get(token);
      if (known !== undefined) return known;
      const value = identifiers.size;
      identifiers.set(token, value);
      return value;
    };
    const leftIds = left.map(identify);
    const rightIds = right.map(identify);
    const width = right.length + 1;
    const lengths = new Uint32Array((left.length + 1) * width);
    for (let leftIndex = left.length - 1; leftIndex >= 0; leftIndex -= 1) {
      for (let rightIndex = right.length - 1; rightIndex >= 0; rightIndex -= 1) {
        const position = leftIndex * width + rightIndex;
        lengths[position] = leftIds[leftIndex] === rightIds[rightIndex]
          ? lengths[position + width + 1] + 1
          : Math.max(lengths[position + width], lengths[position + 1]);
      }
    }
    let leftIndex = 0;
    let rightIndex = 0;
    while (leftIndex < left.length && rightIndex < right.length) {
      if (leftIds[leftIndex] === rightIds[rightIndex]) {
        append('equal', left[leftIndex++]);
        rightIndex += 1;
      } else if (lengths[(leftIndex + 1) * width + rightIndex] >= lengths[leftIndex * width + rightIndex + 1]) {
        append('removed', left[leftIndex++]);
      } else {
        append('added', right[rightIndex++]);
      }
    }
    append('removed', left.slice(leftIndex).join(''));
    append('added', right.slice(rightIndex).join(''));
  }
  append('equal', before.slice(before.length - suffix).join(''));
  return result;
}

export { compareTokens as compareDocumentTokens };
