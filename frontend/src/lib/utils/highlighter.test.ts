import { describe, expect, it } from 'vitest';
import hljs from './highlighter';

describe('highlighter', () => {
  it('注册常用语言与补充语言', () => {
    for (const language of ['typescript', 'python', 'bash', 'json', 'yaml', 'toml', 'dockerfile', 'nginx', 'powershell']) {
      expect(hljs.getLanguage(language), language).toBeTruthy();
    }
  });

  it('不打包冷门语言', () => {
    expect(hljs.getLanguage('cobol')).toBeUndefined();
  });
});
