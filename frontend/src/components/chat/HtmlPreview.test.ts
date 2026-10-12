import { describe, expect, it } from 'vitest';
import { buildSandboxDocument, htmlDownloadFilename } from './HtmlPreview';

const SHIM_MARK = 'localStorage';

describe('buildSandboxDocument', () => {
  it('把存储替身插在 <head> 里，DOCTYPE 仍在最前面', () => {
    const doc = buildSandboxDocument('<!DOCTYPE html><html lang="zh"><head><title>t</title></head><body></body></html>');

    expect(doc.startsWith('<!DOCTYPE html><html lang="zh"><head><script>')).toBe(true);
    expect(doc.indexOf(SHIM_MARK)).toBeLessThan(doc.indexOf('<title>'));
  });

  it('没有 <head> 时插在 <html> 后，都没有时放最前面', () => {
    expect(buildSandboxDocument('<html><body>x</body></html>').startsWith('<html><script>')).toBe(true);
    expect(buildSandboxDocument('<!doctype html><p>x</p>').startsWith('<!doctype html><script>')).toBe(true);
    expect(buildSandboxDocument('<p>x</p>').startsWith('<script>')).toBe(true);
  });

  it('不把 <header> 当成 <head>', () => {
    const doc = buildSandboxDocument('<html><body><header>h</header></body></html>');

    expect(doc.startsWith('<html><script>')).toBe(true);
  });

  it('同时注入高度回报脚本，在页面自己的脚本之前', () => {
    const doc = buildSandboxDocument('<html><head></head><body><script>run()</script></body></html>');

    expect(doc).toContain('fusion-html-preview-height');
    expect(doc.indexOf('fusion-html-preview-height')).toBeLessThan(doc.indexOf('run()'));
  });
});

describe('htmlDownloadFilename', () => {
  it('取页面标题做文件名，去掉文件名里不允许的字符', () => {
    expect(htmlDownloadFilename('<html><head><title>抽奖 &amp; 计分</title></head></html>')).toBe('抽奖 & 计分.html');
    expect(htmlDownloadFilename('<title>a/b:c*d?</title>')).toBe('a b c d.html');
  });

  it('没有标题或标题为空时叫 page.html', () => {
    expect(htmlDownloadFilename('<p>x</p>')).toBe('page.html');
    expect(htmlDownloadFilename('<title>  </title>')).toBe('page.html');
  });
});
