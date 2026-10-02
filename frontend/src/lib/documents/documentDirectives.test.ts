import { describe, expect, it } from 'vitest';
import { parseDocumentDirectives, type DocumentSegment, type DocumentSourcePositions } from './documentDirectives';

function expectSourceText(source: string, displayed: string, offsets: number[] | undefined) {
  expect(offsets).toBeDefined();
  expect(offsets).toHaveLength(displayed.length);
  for (let index = 0; index < displayed.length; index += 1) {
    if (offsets?.[index] === -1) expect(displayed[index]).toBe('\n');
    else expect(source[offsets?.[index] ?? -1]).toBe(displayed[index]);
  }
}

function statsSegment(segment: DocumentSegment) {
  if (segment.kind !== 'stats') throw new Error('expected stats');
  return segment;
}

describe('parseDocumentDirectives', () => {
  it('splits callouts, stats, timeline and nested tabs', () => {
    const segments = parseDocumentDirectives([
      '# 香港三天两夜',
      '',
      ':::stats[预算]',
      '- **总预算**: ¥3,500-4,300',
      '- 天数：3 天',
      ':::',
      '',
      '::::tabs',
      ':::tab[D1]',
      ':::timeline',
      '- **09:00-11:05** 深圳出发',
      ':::',
      ':::',
      ':::tab[D2]',
      ':::warning[出发前]',
      '带好港澳通行证',
      ':::',
      ':::',
      '::::',
      '尾段',
    ].join('\n'));

    expect(segments.map(segment => segment.kind)).toEqual(['markdown', 'stats', 'tabs', 'markdown']);
    expect(segments[1]).toMatchObject({
      kind: 'stats',
      label: '预算',
      items: [
        { label: '总预算', value: '¥3,500-4,300' },
        { label: '天数', value: '3 天' },
      ],
    });
    const tabs = segments[2];
    if (tabs.kind !== 'tabs') throw new Error('expected tabs');
    expect(tabs.tabs.map(tab => tab.label)).toEqual(['D1', 'D2']);
    expect(tabs.tabs[0].children[0]).toMatchObject({ kind: 'timeline' });
    expect(tabs.tabs[1].children[0]).toMatchObject({ kind: 'callout', variant: 'warning', label: '出发前' });
  });

  it('accepts nested containers written entirely with three colons', () => {
    const segments = parseDocumentDirectives([
      ':::tabs',
      ':::tab[D1]',
      '第一天',
      ':::',
      ':::tab[D2]',
      '第二天',
      ':::',
      ':::',
    ].join('\n'));
    expect(segments).toHaveLength(1);
    const tabs = segments[0];
    if (tabs.kind !== 'tabs') throw new Error('expected tabs');
    expect(tabs.tabs.map(tab => tab.children[0])).toEqual([
      { kind: 'markdown', text: '第一天' },
      { kind: 'markdown', text: '第二天' },
    ]);
  });

  it('keeps unknown directives, fenced code and unclosed streaming containers readable', () => {
    const segments = parseDocumentDirectives([
      ':::unknown',
      '```',
      ':::tip',
      '```',
      ':::tip',
      '还没写完',
    ].join('\n'));
    expect(segments[0]).toEqual({ kind: 'markdown', text: ':::unknown\n```\n:::tip\n```' });
    expect(segments[1]).toMatchObject({ kind: 'callout', variant: 'tip', label: null });
  });

  it('falls back to markdown when stats has no label-value items', () => {
    const [segment] = parseDocumentDirectives(':::stats\n一段说明\n:::');
    expect(segment).toMatchObject({ kind: 'stats', items: [], fallback: '一段说明' });
  });

  it('源位置使用 CRLF 规范后 UTF-16 偏移，重复文字分别绑定最终对象', () => {
    const raw = '重复👍🏽\r\n第二行\r\n:::tip[  重复👍🏽  ]\r\n重复👍🏽\r\n:::\r\n重复👍🏽';
    const source = raw.replace(/\r\n/g, '\n');
    const positions: DocumentSourcePositions = new WeakMap();
    const segments = parseDocumentDirectives(raw, positions);
    expect(segments).toEqual(parseDocumentDirectives(raw));
    const [first, callout, last] = segments;
    if (first.kind !== 'markdown' || callout.kind !== 'callout' || last.kind !== 'markdown') throw new Error('expected markdown/callout');
    const child = callout.children[0];
    if (child.kind !== 'markdown') throw new Error('expected child markdown');
    expectSourceText(source, first.text, positions.get(first)?.text);
    expectSourceText(source, callout.label ?? '', positions.get(callout)?.label);
    expectSourceText(source, child.text, positions.get(child)?.text);
    expectSourceText(source, last.text, positions.get(last)?.text);
    expect(positions.get(first)?.text?.[0]).toBe(0);
    expect(positions.get(callout)?.label?.[0]).toBe(source.indexOf('重复👍🏽', first.text.length));
    expect(positions.get(child)?.text?.[0]).toBe(source.indexOf('重复👍🏽', (positions.get(callout)?.label?.at(-1) ?? 0) + 1));
    expect(positions.get(last)?.text?.[0]).toBe(source.lastIndexOf('重复👍🏽'));
  });

  it('统计条目去除内部强调并 trim，标签和值同名时仍指向各自真实字符', () => {
    const source = ':::stats[ 总预算 ]\n- **总**预算__:__  **总**预算__👍🏽__  \n- 总预算：总预算\n:::';
    const positions: DocumentSourcePositions = new WeakMap();
    const [raw] = parseDocumentDirectives(source, positions);
    const segment = statsSegment(raw);
    expect(segment.items).toEqual([{ label: '总预算', value: '总预算👍🏽' }, { label: '总预算', value: '总预算' }]);
    expectSourceText(source, segment.label ?? '', positions.get(segment)?.label);
    expectSourceText(source, segment.fallback, positions.get(segment)?.text);
    for (const item of segment.items) {
      expectSourceText(source, item.label, positions.get(item)?.label);
      expectSourceText(source, item.value, positions.get(item)?.value);
    }
    expect(positions.get(segment)?.label?.[0]).toBe(10);
    expect(positions.get(segment.items[0])?.label).toEqual([20, 23, 24]);
    expect(positions.get(segment.items[0])?.value?.[0]).toBe(34);
    expect(positions.get(segment.items[1])?.label?.[0]).toBe(source.lastIndexOf('- 总预算') + 2);
    expect(positions.get(segment.items[1])?.value?.[0]).toBe(source.lastIndexOf('：') + 1);
  });

  it('同名 tabs、统计标题与条目按递归源位置定位', () => {
    const source = [
      '::::tabs', ':::tab[预算]', ':::stats[预算]', '- 预算：预算', ':::', ':::',
      ':::tab[预算]', ':::timeline[预算]', ':::stats[预算]', '- **预算**：**预算**', ':::', ':::', ':::', '::::',
    ].join('\n');
    const positions: DocumentSourcePositions = new WeakMap();
    const [segment] = parseDocumentDirectives(source, positions);
    if (segment.kind !== 'tabs') throw new Error('expected tabs');
    const first = statsSegment(segment.tabs[0].children[0]);
    const timeline = segment.tabs[1].children[0];
    if (timeline.kind !== 'timeline') throw new Error('expected timeline');
    const second = statsSegment(timeline.children[0]);
    const objects = [segment.tabs[0], first, first.items[0], segment.tabs[1], timeline, second, second.items[0]];
    const labelOffsets = objects.map(object => positions.get(object)?.label?.[0] ?? -1);
    expect(labelOffsets.every(offset => offset >= 0)).toBe(true);
    expect(labelOffsets).toEqual([...labelOffsets].sort((left, right) => left - right));
    expect(new Set(labelOffsets).size).toBe(objects.length);
    for (const object of objects) expectSourceText(source, object.label ?? '', positions.get(object)?.label);
    expectSourceText(source, first.items[0].value, positions.get(first.items[0])?.value);
    expectSourceText(source, second.items[0].value, positions.get(second.items[0])?.value);
    expect(segment).toEqual(parseDocumentDirectives(source)[0]);
  });

  it('统计容器收集嵌套文字时保留字符出处，生成的连接换行为 -1', () => {
    const source = ':::stats[预算]\n- 第一项：重复\n:::tip[重复]\n- 第二项：重复\n:::\n- 第三项：重复\n:::';
    const positions: DocumentSourcePositions = new WeakMap();
    const [raw] = parseDocumentDirectives(source, positions);
    const segment = statsSegment(raw);
    expect(segment.items).toEqual([{ label: '第一项', value: '重复' }, { label: '第二项', value: '重复' }, { label: '第三项', value: '重复' }]);
    expect(segment.fallback).toBe('- 第一项：重复\n- 第二项：重复\n- 第三项：重复');
    expectSourceText(source, segment.fallback, positions.get(segment)?.text);
    expect(positions.get(segment)?.text?.filter(offset => offset === -1)).toHaveLength(2);
    for (const item of segment.items) {
      expectSourceText(source, item.label, positions.get(item)?.label);
      expectSourceText(source, item.value, positions.get(item)?.value);
    }
  });

  it('忽略空 tabs 后合并 Markdown，最终对象的虚拟换行使用 -1', () => {
    const source = '重复\n:::tabs\n:::\n重复';
    const positions: DocumentSourcePositions = new WeakMap();
    const segments = parseDocumentDirectives(source, positions);
    expect(segments).toEqual([{ kind: 'markdown', text: '重复\n重复' }]);
    expect(positions.get(segments[0])?.text).toEqual([0, 1, -1, source.length - 2, source.length - 1]);
    expectSourceText(source, '重复\n重复', positions.get(segments[0])?.text);
  });

  it('未知指令、代码与未闭合流式容器保持原语义和文字位置', () => {
    const source = ':::unknown[重复]\n```\n:::tip[重复]\n```\n:::tip[重复]\n未完成👍🏽';
    const positions: DocumentSourcePositions = new WeakMap();
    const segments = parseDocumentDirectives(source, positions);
    expect(segments).toEqual(parseDocumentDirectives(source));
    const [markdown, callout] = segments;
    if (markdown.kind !== 'markdown' || callout.kind !== 'callout') throw new Error('expected markdown/callout');
    expectSourceText(source, markdown.text, positions.get(markdown)?.text);
    expect(positions.get(callout)?.label?.[0]).toBe(source.lastIndexOf('重复'));
    const child = callout.children[0];
    if (child.kind !== 'markdown') throw new Error('expected markdown');
    expectSourceText(source, child.text, positions.get(child)?.text);
  });

  it('无标签容器和默认 tab 名称不虚构 label 位置', () => {
    const source = '::::tabs\n:::tab\n:::tip\n:::timeline\n:::stats\n- 天数：3 天\n:::\n:::\n:::\n:::\n::::';
    const positions: DocumentSourcePositions = new WeakMap();
    const [segment] = parseDocumentDirectives(source, positions);
    if (segment.kind !== 'tabs') throw new Error('expected tabs');
    const tab = segment.tabs[0];
    expect(tab.label).toBe('1');
    expect(positions.get(tab)?.label).toBeUndefined();
    const callout = tab.children[0];
    if (callout.kind !== 'callout') throw new Error('expected callout');
    const timeline = callout.children[0];
    if (timeline.kind !== 'timeline') throw new Error('expected timeline');
    const stats = statsSegment(timeline.children[0]);
    for (const object of [callout, timeline, stats]) expect(positions.get(object)?.label).toBeUndefined();
    expectSourceText(source, stats.items[0].label, positions.get(stats.items[0])?.label);
    expectSourceText(source, stats.items[0].value, positions.get(stats.items[0])?.value);
  });

  it('不传位置表时返回结构保持精简，并与采集位置的返回值一致', () => {
    const source = ':::stats[预算]\n- **金额**：**100**\n:::\n:::tab[孤立]\n正文\n:::';
    const withoutPositions = parseDocumentDirectives(source);
    const positions: DocumentSourcePositions = new WeakMap();
    const withPositions = parseDocumentDirectives(source, positions);
    expect(withPositions).toEqual(withoutPositions);
    expect(JSON.stringify(withoutPositions)).not.toMatch(/offsets|Position|lineStarts|source/);
    const orphan = withPositions[1];
    if (orphan.kind !== 'callout') throw new Error('expected callout');
    expectSourceText(source, orphan.label ?? '', positions.get(orphan)?.label);
  });
});
