import type { DocumentSegment, DocumentSourcePositions } from './documentDirectives';
import type { DocumentHighlightRange } from './documentDiffHighlights';

/** 容器解析会归一 CRLF；高亮先映射到同一份文本，再按渲染路径投影。 */
export function mapDocumentMarkdownHighlights(source: string, segments: DocumentSegment[], ranges: DocumentHighlightRange[], positions: DocumentSourcePositions) {
  const removed: number[] = [];
  for (let i = 0; i < source.length - 1; i += 1) {
    if (source[i] === '\r' && source[i + 1] === '\n') removed.push(i);
  }
  const normalizeOffset = (offset: number) => {
    let low = 0;
    let high = removed.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (removed[middle] < offset) low = middle + 1;
      else high = middle;
    }
    return offset - low;
  };
  const highlights = ranges.map(range => ({ ...range, start: normalizeOffset(range.start), end: normalizeOffset(range.end) }))
    .filter(range => range.end > range.start);
  const result: Record<string, DocumentHighlightRange[]> = {};
  const project = (path: string, offsets?: number[]) => {
    if (!offsets?.length) return;
    const projected: DocumentHighlightRange[] = [];
    let rangeIndex = 0;
    const first = offsets.find(offset => offset >= 0);
    if (first === undefined) return;
    let high = highlights.length;
    while (rangeIndex < high) {
      const middle = (rangeIndex + high) >>> 1;
      if (highlights[middle].end <= first) rangeIndex = middle + 1;
      else high = middle;
    }
    offsets.forEach((offset, index) => {
      // 虚拟换行没有对应源字符，不能把已忽略的容器变化投影到正文上。
      if (offset < 0) return;
      while (rangeIndex < highlights.length && highlights[rangeIndex].end <= offset) rangeIndex += 1;
      const range = highlights[rangeIndex];
      if (!range || range.start > offset) return;
      const previous = projected.at(-1);
      if (previous?.kind === range.kind && previous.end === index) previous.end = index + 1;
      else projected.push({ start: index, end: index + 1, kind: range.kind });
    });
    result[path] = projected;
  };
  const walk = (items: DocumentSegment[], parent: string) => {
    items.forEach((segment, index) => {
      const path = `${parent}.${index}`;
      const position = positions.get(segment);
      project(`${path}.label`, position?.label);
      if (segment.kind === 'markdown' || segment.kind === 'stats') {
        project(path, position?.text);
        if (segment.kind === 'stats') segment.items.forEach((item, itemIndex) => {
          const position = positions.get(item);
          project(`${path}.item.${itemIndex}.label`, position?.label);
          project(`${path}.item.${itemIndex}.value`, position?.value);
        });
      } else if (segment.kind === 'tabs') {
        segment.tabs.forEach((tab, tabIndex) => {
          const tabPath = `${path}.tab.${tabIndex}`;
          project(`${tabPath}.label`, positions.get(tab)?.label);
          walk(tab.children, tabPath);
        });
      } else {
        walk(segment.children, `${path}.children`);
      }
    });
  };
  walk(segments, 'root');
  return result;
}

interface HighlightNode {
  type: string;
  tagName?: string;
  value?: string;
  children?: HighlightNode[];
  properties?: Record<string, unknown>;
  position?: { start: { offset?: number }; end: { offset?: number } };
}

/** 在已解析的 Markdown 文字上标记，不向模型原文插入 HTML 或拆开 Markdown 语法。 */
export function createDocumentHighlightPlugin(source: string, ranges: DocumentHighlightRange[]) {
  const overlaps = (node: HighlightNode) => {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    if (start === undefined || end === undefined) return [];
    let low = 0;
    let high = ranges.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (ranges[middle].end <= start) low = middle + 1;
      else high = middle;
    }
    const matches = [];
    for (let index = low; index < ranges.length && ranges[index].start < end; index += 1) matches.push(ranges[index]);
    return matches;
  };
  const kindFor = (matches: DocumentHighlightRange[]) => matches.some(range => range.kind === 'modified')
    ? 'modified' : matches[0]?.kind;
  const markedText = (text: string, kind: DocumentHighlightRange['kind']): HighlightNode => ({
    type: 'element', tagName: 'mark', properties: { 'data-document-change': kind }, children: [{ type: 'text', value: text }],
  });
  return () => (tree: unknown) => {
    const visit = (node: HighlightNode, insideCode = false) => {
      const matches = overlaps(node);
      if (node.type === 'element' && matches.length) {
        const tag = node.tagName;
        const first = node.children?.[0]?.position?.start.offset;
        const last = node.children?.at(-1)?.position?.end.offset;
        const changedSyntax = first === undefined || last === undefined
          || matches.some(range => range.start < first || range.end > last);
        if (tag === 'td' || tag === 'th' || tag === 'pre' || tag === 'code'
          || (changedSyntax && (tag === 'a' || tag === 'strong' || tag === 'em' || tag === 'del'))) {
          node.properties = { ...node.properties, 'data-document-change': kindFor(matches) };
        }
      }
      if (!node.children) return;
      const code = insideCode || node.tagName === 'pre' || node.tagName === 'code';
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || code) {
          visit(child, code);
          return [child];
        }
        const matches = overlaps(child);
        if (!matches.length || !child.value) return [child];
        const start = child.position!.start.offset!;
        const end = child.position!.end.offset!;
        // 实体和转义字符的显示长度不同于原文，保留解析后的文字并标记整段。
        if (source.slice(start, end) !== child.value) return [markedText(child.value, kindFor(matches)!)];
        const parts: HighlightNode[] = [];
        let offset = 0;
        for (const range of matches) {
          const left = Math.max(offset, range.start - start);
          const right = Math.min(child.value.length, range.end - start);
          if (right <= left) continue;
          if (left > offset) parts.push({ type: 'text', value: child.value.slice(offset, left) });
          parts.push(markedText(child.value.slice(left, right), range.kind));
          offset = right;
        }
        if (offset < child.value.length) parts.push({ type: 'text', value: child.value.slice(offset) });
        return parts;
      });
    };
    visit(tree as HighlightNode);
  };
}
