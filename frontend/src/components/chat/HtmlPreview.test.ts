import { describe, expect, it } from 'vitest';
import { buildSandboxDocument } from './HtmlPreview';

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
