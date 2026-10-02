/**
 * 交付物文档的容器指令解析：在 Markdown 外层识别 `:::name[label]` … `:::` 块。
 *
 * 只做语法层面的切分，不理解正文语义；未知指令按原文保留为 Markdown，
 * 未闭合的容器（流式预览中途）在文末自动闭合。
 */

export type CalloutVariant = 'tip' | 'info' | 'warning' | 'price';

export interface DocumentStatItem {
  label: string;
  value: string;
}

export type DocumentSegment =
  | { kind: 'markdown'; text: string }
  | { kind: 'callout'; variant: CalloutVariant; label: string | null; children: DocumentSegment[] }
  | { kind: 'timeline'; label: string | null; children: DocumentSegment[] }
  | { kind: 'stats'; label: string | null; items: DocumentStatItem[]; fallback: string }
  | { kind: 'tabs'; tabs: DocumentTab[] };

export interface DocumentTab {
  label: string;
  children: DocumentSegment[];
}

export interface DocumentSourcePosition {
  text?: number[];
  label?: number[];
  value?: number[];
}

export type DocumentSourcePositions = WeakMap<DocumentSegment | DocumentTab | DocumentStatItem, DocumentSourcePosition>;

const CALLOUT_VARIANTS = new Set<CalloutVariant>(['tip', 'info', 'warning', 'price']);
const CONTAINER_NAMES = new Set(['tip', 'info', 'warning', 'price', 'timeline', 'stats', 'tabs', 'tab']);
const OPEN_PATTERN = /^\s{0,3}(:{3,})([a-z]+)(?:\[([^\]\n]*)\])?\s*$/;
const CLOSE_PATTERN = /^\s{0,3}(:{3,})\s*$/;
const FENCE_PATTERN = /^\s{0,3}(`{3,}|~{3,})/;

interface OpenContainer {
  name: string;
  label: string | null;
  colons: number;
  children: RawNode[];
  labelPosition?: number[];
}

type RawNode = { type: 'text'; lines: string[]; lineStarts?: number[] } | { type: 'container'; container: OpenContainer };

interface SourceText {
  text: string;
  offsets?: number[];
}

/** 可选源位置以 CRLF 统一为 LF 后的 UTF-16 字符为单位，不改变显示内容。 */
export function parseDocumentDirectives(markdown: string, positions?: DocumentSourcePositions): DocumentSegment[] {
  const root: OpenContainer = { name: 'root', label: null, colons: Infinity, children: [] };
  const stack: OpenContainer[] = [root];
  let fence: string | null = null;
  let lineStart = 0;

  const appendLine = (line: string, start: number) => {
    const current = stack[stack.length - 1];
    const last = current.children[current.children.length - 1];
    if (last?.type === 'text') {
      last.lines.push(line);
      if (positions) last.lineStarts?.push(start);
    } else {
      current.children.push(positions
        ? { type: 'text', lines: [line], lineStarts: [start] }
        : { type: 'text', lines: [line] });
    }
  };

  for (const line of markdown.replace(/\r\n/g, '\n').split('\n')) {
    const currentStart = lineStart;
    lineStart += line.length + 1;
    if (fence !== null) {
      appendLine(line, currentStart);
      if (line.trimStart().startsWith(fence)) fence = null;
      continue;
    }
    const fenceMatch = FENCE_PATTERN.exec(line);
    if (fenceMatch) {
      fence = fenceMatch[1];
      appendLine(line, currentStart);
      continue;
    }

    const open = OPEN_PATTERN.exec(line);
    if (open && CONTAINER_NAMES.has(open[2])) {
      const container: OpenContainer = {
        name: open[2],
        label: open[3]?.trim() || null,
        colons: open[1].length,
        children: [],
      };
      if (positions && container.label && open[3] !== undefined) {
        const labelStart = currentStart + line.length - line.trimStart().length + open[1].length + open[2].length + 1
          + open[3].length - open[3].trimStart().length;
        container.labelPosition = sourceOffsets(container.label, labelStart);
      }
      stack[stack.length - 1].children.push({ type: 'container', container });
      stack.push(container);
      continue;
    }

    const close = CLOSE_PATTERN.exec(line);
    if (close && stack.length > 1) {
      // 关闭冒号数相同的最近容器（外层 `::::` 顺带闭合漏写结束行的内层）；
      // 冒号数对不上时关闭最内层，兼容全部用 `:::` 书写的嵌套。
      const colons = close[1].length;
      const matched = findLastIndex(stack, item => item !== root && item.colons === colons);
      stack.length = matched > 0 ? matched : stack.length - 1;
      continue;
    }

    appendLine(line, currentStart);
  }

  return toSegments(root.children, positions);
}

function toSegments(nodes: RawNode[], positions?: DocumentSourcePositions): DocumentSegment[] {
  const segments: DocumentSegment[] = [];
  for (const node of nodes) {
    if (node.type === 'text') {
      const text = node.lines.join('\n');
      if (text.trim()) {
        const segment: DocumentSegment = { kind: 'markdown', text };
        segments.push(segment);
        if (positions) positions.set(segment, { text: textNodeOffsets(node) });
      }
      continue;
    }
    const segment = toContainerSegment(node.container, positions);
    if (segment) segments.push(segment);
  }
  return mergeAdjacentMarkdown(segments, positions);
}

function toContainerSegment(container: OpenContainer, positions?: DocumentSourcePositions): DocumentSegment | null {
  const { name, label } = container;
  const withLabel = <T extends DocumentSegment | DocumentTab>(segment: T): T => {
    if (positions && container.labelPosition) positions.set(segment, { label: container.labelPosition });
    return segment;
  };
  if (CALLOUT_VARIANTS.has(name as CalloutVariant)) {
    return withLabel({ kind: 'callout', variant: name as CalloutVariant, label, children: toSegments(container.children, positions) });
  }
  if (name === 'timeline') {
    return withLabel({ kind: 'timeline', label, children: toSegments(container.children, positions) });
  }
  if (name === 'stats') {
    const collected = collectText(container, Boolean(positions));
    const segment: DocumentSegment = { kind: 'stats', label, items: parseStatItems(collected, positions), fallback: collected.text };
    if (positions) positions.set(segment, { text: collected.offsets, ...(container.labelPosition ? { label: container.labelPosition } : {}) });
    return segment;
  }
  if (name === 'tabs') {
    const tabs: DocumentTab[] = [];
    for (const child of container.children) {
      if (child.type === 'container' && child.container.name === 'tab') {
        const tab: DocumentTab = {
          label: child.container.label ?? `${tabs.length + 1}`,
          children: toSegments(child.container.children, positions),
        };
        tabs.push(tab);
        if (positions && child.container.labelPosition) positions.set(tab, { label: child.container.labelPosition });
      }
    }
    return tabs.length > 0 ? { kind: 'tabs', tabs } : null;
  }
  // 孤立的 tab 不在 tabs 内时按普通段落展示，避免内容丢失。
  if (name === 'tab') {
    const children = toSegments(container.children, positions);
    return withLabel({ kind: 'callout', variant: 'info', label, children });
  }
  return null;
}

function collectText(container: OpenContainer, withPositions: boolean): SourceText {
  const children = container.children.map(child => child.type === 'text'
    ? { text: child.lines.join('\n'), ...(withPositions ? { offsets: textNodeOffsets(child) } : {}) }
    : collectText(child.container, withPositions));
  return {
    text: children.map(child => child.text).join('\n'),
    ...(withPositions ? { offsets: children.flatMap((child, index) => index === 0 ? child.offsets ?? [] : [-1, ...(child.offsets ?? [])]) } : {}),
  };
}

function parseStatItems(source: SourceText, positions?: DocumentSourcePositions): DocumentStatItem[] {
  const items: DocumentStatItem[] = [];
  let lineStart = 0;
  for (const line of source.text.split('\n')) {
    const currentStart = lineStart;
    lineStart += line.length + 1;
    const item = /^\s*[-*+]\s+(.+)$/.exec(line)?.[1];
    if (!item) continue;
    const separator = item.search(/[:：]/);
    if (separator <= 0) continue;
    const itemStart = currentStart + line.length - item.length;
    const label = stripEmphasis(item.slice(0, separator), source.offsets?.slice(itemStart, itemStart + separator));
    const value = stripEmphasis(item.slice(separator + 1), source.offsets?.slice(itemStart + separator + 1, currentStart + line.length));
    if (label.text && value.text) {
      const stat: DocumentStatItem = { label: label.text, value: value.text };
      items.push(stat);
      if (positions) positions.set(stat, { label: label.offsets, value: value.offsets });
    }
  }
  return items;
}

function stripEmphasis(value: string, offsets?: number[]): SourceText {
  if (!offsets) return { text: value.replace(/\*\*|__/g, '').trim() };
  let text = '';
  const kept: number[] = [];
  for (let index = 0; index < value.length; index += 1) {
    if (value.startsWith('**', index) || value.startsWith('__', index)) index += 1;
    else {
      text += value[index];
      kept.push(offsets[index]);
    }
  }
  const start = text.length - text.trimStart().length;
  const end = text.trimEnd().length;
  return { text: text.trim(), offsets: kept.slice(start, end) };
}

function mergeAdjacentMarkdown(segments: DocumentSegment[], positions?: DocumentSourcePositions): DocumentSegment[] {
  const merged: DocumentSegment[] = [];
  for (const segment of segments) {
    const last = merged[merged.length - 1];
    if (segment.kind === 'markdown' && last?.kind === 'markdown') {
      const combined: DocumentSegment = { kind: 'markdown', text: `${last.text}\n${segment.text}` };
      merged[merged.length - 1] = combined;
      if (positions) positions.set(combined, { text: [...(positions.get(last)?.text ?? []), -1, ...(positions.get(segment)?.text ?? [])] });
    } else {
      merged.push(segment);
    }
  }
  return merged;
}

function sourceOffsets(text: string, start: number): number[] {
  const offsets: number[] = [];
  for (let index = 0; index < text.length; index += 1) offsets.push(start + index);
  return offsets;
}

function textNodeOffsets(node: Extract<RawNode, { type: 'text' }>): number[] {
  const offsets: number[] = [];
  for (let index = 0; index < node.lines.length; index += 1) {
    const start = node.lineStarts?.[index] ?? -1;
    if (index > 0) {
      const previousEnd = (node.lineStarts?.[index - 1] ?? -1) + node.lines[index - 1].length;
      offsets.push(previousEnd + 1 === start ? previousEnd : -1);
    }
    for (let character = 0; character < node.lines[index].length; character += 1) offsets.push(start < 0 ? -1 : start + character);
  }
  return offsets;
}

function findLastIndex<T>(items: T[], predicate: (item: T) => boolean): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index])) return index;
  }
  return -1;
}
