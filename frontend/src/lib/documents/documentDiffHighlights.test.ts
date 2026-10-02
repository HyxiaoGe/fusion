import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildDocumentHighlights, type DocumentHighlightRange, type DocumentHighlights } from './documentDiffHighlights';

function highlighted(text: string, ranges: DocumentHighlightRange[]) {
  return ranges.map(range => ({ text: text.slice(range.start, range.end), kind: range.kind }));
}

function expectValid(before: string, after: string, result: DocumentHighlights) {
  for (const side of ['before', 'after'] as const) {
    const text = side === 'before' ? before : after;
    let previousEnd = 0;
    for (const range of result[side]) {
      expect(range.start).toBeGreaterThanOrEqual(previousEnd);
      expect(range.end).toBeGreaterThan(range.start);
      expect(range.end).toBeLessThanOrEqual(text.length);
      expect(range.start > 0 && /[\uD800-\uDBFF]/u.test(text[range.start - 1]) && /[\uDC00-\uDFFF]/u.test(text[range.start])).toBe(false);
      expect(range.end < text.length && /[\uD800-\uDBFF]/u.test(text[range.end - 1]) && /[\uDC00-\uDFFF]/u.test(text[range.end])).toBe(false);
      previousEnd = range.end;
    }
  }
}

afterEach(() => vi.unstubAllGlobals());

describe('buildDocumentHighlights', () => {
  it('相同正文和空正文不生成范围', () => {
    for (const text of ['', '正文\n\n尾段  ', '|列|值|\n|---|---|\n|1|2|\n']) {
      expect(buildDocumentHighlights(text, text)).toEqual({ before: [], after: [] });
    }
  });

  it('普通短文字修改只标记实际变化的字素', () => {
    const before = '杭州两日游\n\n周末一起去西湖。';
    const after = '杭州三日游\n\n周末一起去西溪。';
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '两', kind: 'modified' }, { text: '湖', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '三', kind: 'modified' }, { text: '溪', kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('新增或删除整行分别使用 added 和 removed', () => {
    const before = '开头\n旧段落\n共同段落\n结尾';
    const after = '开头\n共同段落\n新增段落\n结尾';
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '旧段落', kind: 'removed' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '新增段落', kind: 'added' }]);
    expect(buildDocumentHighlights('', '新增')).toEqual({ before: [], after: [{ start: 0, end: 2, kind: 'added' }] });
    expect(buildDocumentHighlights('删除', '')).toEqual({ before: [{ start: 0, end: 2, kind: 'removed' }], after: [] });
    expectValid(before, after, result);
  });

  it('列表只新增午餐时，原有各项保持无范围', () => {
    const before = '# 第五天\n\n- [ ] 更新电脑安全设置（锁屏、备份、密码管理）\n- [ ] 整理工位与文件，归档本周资料\n- [ ] 填写 HR 满意度/入职体验反馈（如有）\n';
    const after = `${before}- [ ] 和入职引导人约一次午餐\n`;
    const result = buildDocumentHighlights(before, after);
    expect(result.before).toEqual([]);
    expect(highlighted(after, result.after)).toEqual([{ text: '- [ ] 和入职引导人约一次午餐', kind: 'added' }]);
  });

  it('Day 3 总览表三格变化只标记对应单元格，保留表头和其余行', () => {
    const before = '# 首周清单\n\n| 天数 | 主题 | 核心产出 | 关键对接人 |\n| --- | --- | --- | --- |\n| Day 1（周一） | 入职 | 熟悉团队 | HR |\n| Day 2（周二） | 工具 | 配置环境 | 导师 |\n| Day 3（周三） | 深入岗位职责 | 明确岗位职责与考核指标、完成首批小任务 | 直属主管 |\n| Day 4（周四） | 实践 | 熟悉流程 | 导师 |\n| Day 5（周五） | 总结 | 制定计划 | 团队 |\n';
    const after = before.replace('| Day 3（周三） | 深入岗位职责 | 明确岗位职责与考核指标、完成首批小任务 | 直属主管 |', '| Day 3（周三） | 参加部门新人培训 | 完成部门新人培训、整理培训笔记 | 培训讲师、直属主管 |');
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([
      { text: '深入岗位职责', kind: 'modified' }, { text: '明确岗位职责与考核指标、完成首批小任务', kind: 'modified' }, { text: '直属主管', kind: 'modified' },
    ]);
    expect(highlighted(after, result.after)).toEqual([
      { text: '参加部门新人培训', kind: 'modified' }, { text: '完成部门新人培训、整理培训笔记', kind: 'modified' }, { text: '培训讲师、直属主管', kind: 'modified' },
    ]);
    expectValid(before, after, result);
  });

  it('表格新增和删除行不误标后续相同行，包括重复行', () => {
    const header = '| 日期 | 内容 |\n| --- | --- |\n';
    const before = `${header}| Day 1 | 入职 |\n| Day 2 | 旧任务 |\n| Day 3 | 培训 |\n| Day 3 | 培训 |\n| Day 5 | 总结 |\n`;
    const after = `${header}| Day 1 | 入职 |\n| Day 3 | 培训 |\n| Day 3 | 培训 |\n| Day 4 | 新任务 |\n| Day 5 | 总结 |\n`;
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '| Day 2 | 旧任务 |', kind: 'removed' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '| Day 4 | 新任务 |', kind: 'added' }]);
    expect(buildDocumentHighlights(before, after)).toEqual(result);
    expectValid(before, after, result);
  });

  it('表格同时插入和修改相邻行时，以首列配对修改行', () => {
    const before = '| 日期 | 内容 |\n| --- | --- |\n| Day 1 | 入职 |\n| Day 3 | 旧培训 |\n| Day 4 | 总结 |\n';
    const after = before.replace('| Day 3 | 旧培训 |', '| Day 2 | 新任务 |\n| Day 3 | 新培训 |');
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '旧培训', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '| Day 2 | 新任务 |', kind: 'added' }, { text: '新培训', kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('空单元格与转义竖线保持真实 offsets，并忽略单元格边界空格', () => {
    const before = '| 日期 | 内容 | 备注 |\n| --- | --- | --- |\n| Day 3 | 产品\\|培训 |    |\n';
    const after = '| 日期 | 内容 | 备注 |\n| --- | --- | --- |\n| Day 3  |  协作\\|培训  | 新增说明 |\n';
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '产品\\|培训', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '协作\\|培训', kind: 'modified' }, { text: '新增说明', kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('偶数反斜线后的竖线仍是分隔符，无外围竖线的表格也按单元格比较', () => {
    const before = '日期 | 内容 | 备注\n--- | --- | ---\nDay 3 | 路径\\\\| 旧说明\n';
    const after = before.replace('旧说明', '新说明');
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '旧说明', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '新说明', kind: 'modified' }]);
  });

  it('GFM 单个连字符与对齐分隔行仍按表格单元格比较', () => {
    const before = '| 日期 | 内容 |\n| :- | -: |\n| Day 3 | 旧培训 |\n';
    const after = before.replace('旧培训', '新培训');
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '旧培训', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '新培训', kind: 'modified' }]);
  });

  it('调用方统一 CRLF 后，以同一份 LF 文字定位和渲染', () => {
    const rawBefore = '前言\r\n\r\n| 日期 | 内容 |\r\n| --- | --- |\r\n| Day 3 | 旧培训 |\r\n';
    const rawAfter = rawBefore.replace('旧培训', '新培训');
    const before = rawBefore.replace(/\r\n/g, '\n');
    const after = rawAfter.replace(/\r\n/g, '\n');
    const result = buildDocumentHighlights(before, after);
    expect(result.before).toEqual([{ start: before.indexOf('旧培训'), end: before.indexOf('旧培训') + 3, kind: 'modified' }]);
    expect(result.after).toEqual([{ start: after.indexOf('新培训'), end: after.indexOf('新培训') + 3, kind: 'modified' }]);
    expectValid(before, after, result);
    const rawResult = buildDocumentHighlights(rawBefore, rawAfter);
    expect(rawResult.before).toEqual([{ start: rawBefore.indexOf('旧培训'), end: rawBefore.indexOf('旧培训') + 3, kind: 'modified' }]);
    expect(rawResult.after).toEqual([{ start: rawAfter.indexOf('新培训'), end: rawAfter.indexOf('新培训') + 3, kind: 'modified' }]);
    expectValid(rawBefore, rawAfter, rawResult);
  });

  it('代码围栏内的竖线按普通原文比较，不误判为 GFM 表格', () => {
    const before = '```md\n| 日期 | 内容 |\n| --- | --- |\n| Day 3 | 旧培训 |\n```';
    const after = before.replace('旧培训', '新培训');
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '旧', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '新', kind: 'modified' }]);
  });

  it('短文字保持完整肤色 emoji、ZWJ 家庭和组合字符', () => {
    const before = '行程👩‍👩‍👧‍👦👍🏽é结束';
    const after = '行程👩‍👩‍👧‍👦👍🏻è结束';
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '👍🏽é', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '👍🏻è', kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('缺少 Intl.Segmenter 时仍保留 emoji 代理对和组合序列', () => {
    vi.stubGlobal('Intl', { ...Intl, Segmenter: undefined });
    const before = '👍🏽👩‍👩‍👧‍👦🇨🇳é';
    const after = '👍🏻👨‍👩‍👧‍👦🇭🇰è';
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: before, kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: after, kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('长单行降级保留共同前后文，并把变化的 emoji 扩展到完整字素', () => {
    const prefix = '共同开头'.repeat(2_000);
    const suffix = '共同结尾'.repeat(2_000);
    const before = `${prefix}👍${suffix}`;
    const after = `${prefix}👍🏽${suffix}`;
    const result = buildDocumentHighlights(before, after);
    expect(highlighted(before, result.before)).toEqual([{ text: '👍', kind: 'modified' }]);
    expect(highlighted(after, result.after)).toEqual([{ text: '👍🏽', kind: 'modified' }]);
    expectValid(before, after, result);
  });

  it('60,000 字长文限制比较开销，保留共同外侧和每行的共同内容', () => {
    const oldBody = Array.from({ length: 7_500 }, (_, index) => `甲${String(index).padStart(5, '0')}\n\n`).join('');
    const before = `共同开头\n\n${oldBody}共同结尾`;
    const after = before.replaceAll('甲', '乙');
    const started = performance.now();
    const result = buildDocumentHighlights(before, after);
    expect(performance.now() - started).toBeLessThan(2_000);
    expect(result.before).toHaveLength(7_500);
    expect(result.after).toHaveLength(7_500);
    expect(highlighted(before, result.before).every(range => range.text === '甲' && range.kind === 'modified')).toBe(true);
    expect(highlighted(after, result.after).every(range => range.text === '乙' && range.kind === 'modified')).toBe(true);
    expectValid(before, after, result);
  });

  it('源文字内 HTML、Markdown 符号和多种重复行始终只返回有效范围', () => {
    const chunks = ['<script>alert(1)</script>', '**重点**', '| A | B |\n| --- | --- |\n| 一 | 二 |', '重复\n重复', '👨‍👩‍👧‍👦', '', '    代码'];
    for (let index = 0; index < 60; index += 1) {
      const before = Array.from({ length: index % 9 }, (_, item) => chunks[(index + item * 3) % chunks.length]).join('\n');
      const after = Array.from({ length: index % 11 }, (_, item) => chunks[(index * 2 + item * 5) % chunks.length]).join('\n');
      const result = buildDocumentHighlights(before, after);
      expect(Object.keys(result)).toEqual(['before', 'after']);
      expectValid(before, after, result);
    }
  });
});
