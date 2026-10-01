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
}

type RawNode = { type: 'text'; lines: string[] } | { type: 'container'; container: OpenContainer };

export function parseDocumentDirectives(markdown: string): DocumentSegment[] {
  const root: OpenContainer = { name: 'root', label: null, colons: Infinity, children: [] };
  const stack: OpenContainer[] = [root];
  let fence: string | null = null;

  const appendLine = (line: string) => {
    const current = stack[stack.length - 1];
    const last = current.children[current.children.length - 1];
    if (last?.type === 'text') {
      last.lines.push(line);
    } else {
      current.children.push({ type: 'text', lines: [line] });
    }
  };

  for (const line of markdown.replace(/\r\n/g, '\n').split('\n')) {
    if (fence !== null) {
      appendLine(line);
      if (line.trimStart().startsWith(fence)) fence = null;
      continue;
    }
    const fenceMatch = FENCE_PATTERN.exec(line);
    if (fenceMatch) {
      fence = fenceMatch[1];
      appendLine(line);
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

    appendLine(line);
  }

  return toSegments(root.children);
}

function toSegments(nodes: RawNode[]): DocumentSegment[] {
  const segments: DocumentSegment[] = [];
  for (const node of nodes) {
    if (node.type === 'text') {
      const text = node.lines.join('\n');
      if (text.trim()) segments.push({ kind: 'markdown', text });
      continue;
    }
    const segment = toContainerSegment(node.container);
    if (segment) segments.push(segment);
  }
  return mergeAdjacentMarkdown(segments);
}

function toContainerSegment(container: OpenContainer): DocumentSegment | null {
  const { name, label } = container;
  if (CALLOUT_VARIANTS.has(name as CalloutVariant)) {
    return { kind: 'callout', variant: name as CalloutVariant, label, children: toSegments(container.children) };
  }
  if (name === 'timeline') {
    return { kind: 'timeline', label, children: toSegments(container.children) };
  }
  if (name === 'stats') {
    const text = collectText(container);
    return { kind: 'stats', label, items: parseStatItems(text), fallback: text };
  }
  if (name === 'tabs') {
    const tabs: DocumentTab[] = [];
    for (const child of container.children) {
      if (child.type === 'container' && child.container.name === 'tab') {
        tabs.push({
          label: child.container.label ?? `${tabs.length + 1}`,
          children: toSegments(child.container.children),
        });
      }
    }
    return tabs.length > 0 ? { kind: 'tabs', tabs } : null;
  }
  // 孤立的 tab 不在 tabs 内时按普通段落展示，避免内容丢失。
  if (name === 'tab') {
    const children = toSegments(container.children);
    return { kind: 'callout', variant: 'info', label, children };
  }
  return null;
}

function collectText(container: OpenContainer): string {
  return container.children
    .map(child => (child.type === 'text' ? child.lines.join('\n') : collectText(child.container)))
    .join('\n');
}

function parseStatItems(text: string): DocumentStatItem[] {
  const items: DocumentStatItem[] = [];
  for (const line of text.split('\n')) {
    const item = /^\s*[-*+]\s+(.+)$/.exec(line)?.[1];
    if (!item) continue;
    const separator = item.search(/[:：]/);
    if (separator <= 0) continue;
    const label = stripEmphasis(item.slice(0, separator));
    const value = stripEmphasis(item.slice(separator + 1));
    if (label && value) items.push({ label, value });
  }
  return items;
}

function stripEmphasis(value: string): string {
  return value.replace(/\*\*|__/g, '').trim();
}

function mergeAdjacentMarkdown(segments: DocumentSegment[]): DocumentSegment[] {
  const merged: DocumentSegment[] = [];
  for (const segment of segments) {
    const last = merged[merged.length - 1];
    if (segment.kind === 'markdown' && last?.kind === 'markdown') {
      merged[merged.length - 1] = { kind: 'markdown', text: `${last.text}\n${segment.text}` };
    } else {
      merged.push(segment);
    }
  }
  return merged;
}

function findLastIndex<T>(items: T[], predicate: (item: T) => boolean): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index])) return index;
  }
  return -1;
}
