import { beforeEach, describe, expect, it, vi } from 'vitest';
import { readDocumentReadingPosition, writeDocumentReadingPosition, type DocumentReadingPosition } from './documentReadingStorage';

const position: DocumentReadingPosition = { scrollTop: 640, headingKey: 'root.0-24', headingOffset: 32, tabs: { 'root.1': 1 } };

describe('文档阅读位置存储', () => {
  beforeEach(() => { window.sessionStorage.clear(); vi.restoreAllMocks(); });

  it('按用户、文档、版本隔离，元组中的分隔符不会碰撞', () => {
    writeDocumentReadingPosition('user:a', 'doc:b', 1, position);
    expect(readDocumentReadingPosition('user:a', 'doc:b', 1)).toEqual(position);
    expect(readDocumentReadingPosition('user', 'a:doc:b', 1)).toBeNull();
    expect(readDocumentReadingPosition('other-user', 'doc:b', 1)).toBeNull();
    expect(readDocumentReadingPosition('user:a', 'other-doc', 1)).toBeNull();
    expect(readDocumentReadingPosition('user:a', 'doc:b', 2)).toBeNull();
    expect(readDocumentReadingPosition(null, 'doc:b', 1)).toBeNull();
  });

  it('损坏数据和无效坐标降级为从顶部阅读', () => {
    const key = 'fusion:document-reading:["user","doc",1]';
    for (const value of ['{', 'null', JSON.stringify({ ...position, scrollTop: -1 }),
      JSON.stringify({ ...position, headingOffset: '32' }), JSON.stringify({ ...position, tabs: { 'root.1': -1 } }),
      JSON.stringify({ ...position, tabs: { '__proto__': 1, invalid_path: 0 } })]) {
      window.sessionStorage.setItem(key, value);
      expect(readDocumentReadingPosition('user', 'doc', 1)).toBeNull();
    }
  });

  it('存储不可用时不阻断阅读，也不向匿名身份保存', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('禁止存储'); });
    expect(() => writeDocumentReadingPosition('user', 'doc', 1, position)).not.toThrow();
    setItem.mockClear();
    writeDocumentReadingPosition(null, 'doc', 1, position);
    expect(setItem).not.toHaveBeenCalled();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('禁止存储'); });
    expect(readDocumentReadingPosition('user', 'doc', 1)).toBeNull();
  });
});
