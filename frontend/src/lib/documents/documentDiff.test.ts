import { describe, expect, it } from 'vitest';
import { buildDocumentDiff, diffDocumentText, type DocumentDiffResult, type DocumentTextDiffSegment } from './documentDiff';

function reconstructBody(result: DocumentDiffResult, side: 'before' | 'after'): string {
  return result.segments.map(segment => segment.kind === 'equal'
    ? segment.text
    : segment.scope === 'body' ? segment[side] : '').join('');
}

function reconstructText(result: DocumentTextDiffSegment[], side: 'before' | 'after'): string {
  return result.filter(segment => segment.kind === 'equal' || segment.kind === (side === 'before' ? 'removed' : 'added'))
    .map(segment => segment.text).join('');
}

describe('buildDocumentDiff', () => {
  it('keeps identical documents unchanged, including empty content', () => {
    expect(buildDocumentDiff({ title: '攻略', content: '正文\r\n\r\n' }, { title: '攻略', content: '正文\r\n\r\n' }))
      .toEqual({ segments: [{ kind: 'equal', text: '正文\r\n\r\n' }], changeCount: 0 });
    expect(buildDocumentDiff({ title: '', content: '' }, { title: '', content: '' }))
      .toEqual({ segments: [], changeCount: 0 });
  });

  it('places the title change first and gives separate body changes continuous navigation indexes', () => {
    const result = buildDocumentDiff(
      { title: '杭州两日游', content: '第一天去西湖。\n\n第二天不变。\n\n第三天去灵隐寺。' },
      { title: '杭州三日游', content: '第一天去西溪。\n\n第二天不变。\n\n第三天去运河。' },
    );
    expect(result.changeCount).toBe(3);
    expect(result.segments.filter(segment => segment.kind === 'change')).toEqual([
      { kind: 'change', scope: 'title', before: '杭州两日游', after: '杭州三日游', index: 0 },
      { kind: 'change', scope: 'body', before: '第一天去西湖。\n', after: '第一天去西溪。\n', index: 1 },
      { kind: 'change', scope: 'body', before: '第三天去灵隐寺。', after: '第三天去运河。', index: 2 },
    ]);
  });

  it('keeps pure additions and deletions, including deleting the entire body', () => {
    const before = '开头\n\n旧段落\n\n共同段落\n\n结尾';
    const after = '开头\n\n共同段落\n\n新增段落\n\n结尾';
    const result = buildDocumentDiff({ title: '', content: before }, { title: '', content: after });
    const changes = result.segments.filter(segment => segment.kind === 'change');
    expect(changes.some(segment => segment.before.includes('旧段落') && segment.after === '')).toBe(true);
    expect(changes.some(segment => segment.after.includes('新增段落') && segment.before === '')).toBe(true);
    expect(reconstructBody(result, 'before')).toBe(before);
    expect(reconstructBody(result, 'after')).toBe(after);
    expect(buildDocumentDiff({ title: '', content: '全部删除' }, { title: '', content: '' })).toEqual({
      segments: [{ kind: 'change', scope: 'body', before: '全部删除', after: '', index: 0 }], changeCount: 1,
    });
  });

  it('shows whitespace and line-ending changes without normalizing the saved originals', () => {
    const pairs = [
      ['正文\r\n\r\n尾段  ', '正文\n\n尾段 '],
      ['正文\n \n尾段', '正文\n\n尾段'],
      ['正文\n', '正文'],
      ['\t \r\n', ''],
    ];
    for (const [before, after] of pairs) {
      const result = buildDocumentDiff({ title: '', content: before }, { title: '', content: after });
      expect(result.changeCount).toBeGreaterThan(0);
      expect(reconstructBody(result, 'before')).toBe(before);
      expect(reconstructBody(result, 'after')).toBe(after);
    }
  });

  it('keeps fenced code with internal empty lines and directive-looking text in a complete block', () => {
    const code = '````ts\nconst place = "西湖";\n\n:::tip\n```\n````\n';
    const changed = code.replace('西湖', '西溪');
    const result = buildDocumentDiff({ title: '', content: `前言\n\n${code}\n结尾` }, { title: '', content: `前言\n\n${changed}\n结尾` });
    expect(result.segments.filter(segment => segment.kind === 'change')).toEqual([
      { kind: 'change', scope: 'body', before: code, after: changed, index: 0 },
    ]);
  });

  it('keeps a changed Markdown table header, separator and all rows together', () => {
    const table = '| 时间 | 地点 |\n| --- | :---: |\n| 上午 | 西湖 |\n| 下午 | 灵隐寺 |\n';
    const changed = table.replace('灵隐寺', '西溪');
    const result = buildDocumentDiff({ title: '', content: `${table}\n尾段` }, { title: '', content: `${changed}\n尾段` });
    expect(result.segments[0]).toEqual({ kind: 'change', scope: 'body', before: table, after: changed, index: 0 });
  });

  it('keeps setext headings with their underline instead of splitting Markdown syntax', () => {
    for (const underline of ['---', '===']) {
      const before = `旧标题\n${underline}\n正文不变`;
      const after = `新标题\n${underline}\n正文不变`;
      expect(buildDocumentDiff({ title: '', content: before }, { title: '', content: after }).segments).toEqual([
        { kind: 'change', scope: 'body', before: `旧标题\n${underline}\n`, after: `新标题\n${underline}\n`, index: 0 },
        { kind: 'equal', text: '正文不变' },
      ]);
    }
  });

  it('keeps loose and nested list items in the complete list block', () => {
    const list = '- 第一天\n  - 西湖\n\n- 第二天\n  继续游览\n';
    const changed = list.replace('西湖', '西溪');
    const result = buildDocumentDiff({ title: '', content: `${list}\n结尾` }, { title: '', content: `${changed}\n结尾` });
    expect(result.segments[0]).toEqual({ kind: 'change', scope: 'body', before: list, after: changed, index: 0 });
  });

  it('keeps nested custom containers complete, including code fences and repeated colon sizes', () => {
    const container = '::::tabs\n:::tab[第一天]\n:::tip[提醒]\n西湖\n\n~~~md\n::::\n~~~\n:::\n:::\n:::tab[第二天]\n西溪\n:::\n::::\n';
    const changed = container.replace('西湖', '灵隐寺');
    const result = buildDocumentDiff({ title: '', content: `# 行程\n\n${container}\n尾段` }, { title: '', content: `# 行程\n\n${changed}\n尾段` });
    expect(result.segments.filter(segment => segment.kind === 'change')).toEqual([
      { kind: 'change', scope: 'body', before: container, after: changed, index: 0 },
    ]);
  });

  it('retains unclosed containers and fences through the end of the document', () => {
    for (const before of [':::tip\n西湖\n\n末尾', '~~~\n西湖\n\n末尾']) {
      const after = before.replace('西湖', '西溪');
      expect(buildDocumentDiff({ title: '', content: before }, { title: '', content: after }).segments)
        .toEqual([{ kind: 'change', scope: 'body', before, after, index: 0 }]);
    }
  });

  it('aligns repeated blocks deterministically and reconstructs both original documents', () => {
    const before = '重复\n\n重复\n\n旧段\n\n重复\n\n结尾';
    const after = '重复\n\n新段\n\n重复\n\n重复\n\n结尾';
    const result = buildDocumentDiff({ title: '', content: before }, { title: '', content: after });
    expect(buildDocumentDiff({ title: '', content: before }, { title: '', content: after })).toEqual(result);
    expect(reconstructBody(result, 'before')).toBe(before);
    expect(reconstructBody(result, 'after')).toBe(after);
    expect(result.segments.filter(segment => segment.kind === 'change').map(segment => segment.index))
      .toEqual(Array.from({ length: result.changeCount }, (_, index) => index));
  });

  it('reconstructs varied raw Markdown documents exactly across additions, removals and replacements', () => {
    const blocks = ['正文\n\n', '# 标题\r\n', ':::tip\n提醒\n:::\n', '- 地点\n  说明\n', '```js\nconst a = 1;\n```\n', ' \n', '|A|B|\n|---|---|\n|1|2|\n'];
    for (let index = 0; index < 70; index += 1) {
      const before = Array.from({ length: index % 11 }, (_, offset) => blocks[(index + offset * 3) % blocks.length]).join('');
      const after = Array.from({ length: index % 13 }, (_, offset) => blocks[(index * 2 + offset * 5) % blocks.length]).join('');
      const result = buildDocumentDiff({ title: '', content: before }, { title: '', content: after });
      expect(reconstructBody(result, 'before')).toBe(before);
      expect(reconstructBody(result, 'after')).toBe(after);
    }
  });

  it('bounds a 60,000-character worst-case comparison and keeps shared outer content', () => {
    const before = Array.from({ length: 7_500 }, (_, index) => `甲${String(index).padStart(5, '0')}\n\n`).join('');
    const after = before.replaceAll('甲', '乙');
    expect(before.length).toBe(60_000);
    const started = performance.now();
    const result = buildDocumentDiff({ title: '', content: `共同开头\n\n${before}共同结尾` }, { title: '', content: `共同开头\n\n${after}共同结尾` });
    expect(performance.now() - started).toBeLessThan(2_000);
    expect(result.changeCount).toBe(1);
    expect(result.segments[0]).toEqual({ kind: 'equal', text: '共同开头\n\n' });
    expect(result.segments.at(-1)).toEqual({ kind: 'equal', text: '\n共同结尾' });
    expect(reconstructBody(result, 'before')).toBe(`共同开头\n\n${before}共同结尾`);
    expect(reconstructBody(result, 'after')).toBe(`共同开头\n\n${after}共同结尾`);
  });
});

describe('diffDocumentText', () => {
  it('highlights a small Chinese change rather than marking the whole title as modified', () => {
    expect(diffDocumentText('杭州两日游', '杭州三日游')).toEqual([
      { kind: 'equal', text: '杭州' }, { kind: 'removed', text: '两' }, { kind: 'added', text: '三' }, { kind: 'equal', text: '日游' },
    ]);
  });

  it('preserves whole Unicode graphemes, including emoji modifiers and joining sequences', () => {
    const result = diffDocumentText('行程👩‍👩‍👧‍👦👍🏽结束', '行程👩‍👩‍👧‍👦👍🏻结束');
    expect(result).toEqual([
      { kind: 'equal', text: '行程👩‍👩‍👧‍👦' }, { kind: 'removed', text: '👍🏽' }, { kind: 'added', text: '👍🏻' }, { kind: 'equal', text: '结束' },
    ]);
    expect(reconstructText(result, 'before')).toBe('行程👩‍👩‍👧‍👦👍🏽结束');
    expect(reconstructText(result, 'after')).toBe('行程👩‍👩‍👧‍👦👍🏻结束');
  });

  it('retains whitespace, additions and removals in inline comparison', () => {
    for (const [before, after] of [['a  b\n', 'a b\n'], ['', '新增'], ['删除', ''], ['abcabc', 'abcxyzabc']]) {
      const result = diffDocumentText(before, after);
      expect(reconstructText(result, 'before')).toBe(before);
      expect(reconstructText(result, 'after')).toBe(after);
    }
    expect(diffDocumentText('', '')).toEqual([]);
  });

  it('falls back to whole text for complex Markdown or oversized content', () => {
    for (const before of ['```ts\n1\n```', '|列|值|\n|---|---|\n|1|2|', ':::tip\n1\n:::', '- 1\n- 2', '甲'.repeat(2_000)]) {
      const after = `${before}改`;
      expect(diffDocumentText(before, after)).toEqual([{ kind: 'removed', text: before }, { kind: 'added', text: after }]);
    }
  });
});
