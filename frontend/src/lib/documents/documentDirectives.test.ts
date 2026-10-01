import { describe, expect, it } from 'vitest';
import { parseDocumentDirectives } from './documentDirectives';

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
});
