const PREFIX = 'fusion:document-reading:';

export interface DocumentReadingPosition {
  scrollTop: number;
  headingKey: string | null;
  headingOffset: number;
  tabs: Record<string, number>;
}

function storageKey(identity: string | null, documentId: string, version: number): string | null {
  if (!identity || !documentId || !Number.isInteger(version) || version < 1) return null;
  return `${PREFIX}${JSON.stringify([identity, documentId, version])}`;
}

export function readDocumentReadingPosition(identity: string | null, documentId: string, version: number): DocumentReadingPosition | null {
  const key = storageKey(identity, documentId, version);
  if (!key || typeof window === 'undefined') return null;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(key) ?? 'null');
    if (!value || typeof value !== 'object') return null;
    const position = value as Partial<DocumentReadingPosition>;
    if (typeof position.scrollTop !== 'number' || !Number.isFinite(position.scrollTop) || position.scrollTop < 0
      || typeof position.headingOffset !== 'number' || !Number.isFinite(position.headingOffset)
      || !(position.headingKey === null || (typeof position.headingKey === 'string' && /^[a-z0-9.-]{1,200}$/.test(position.headingKey)))
      || !position.tabs || typeof position.tabs !== 'object' || Array.isArray(position.tabs)) return null;
    const entries = Object.entries(position.tabs);
    if (entries.length > 128 || entries.some(([path, index]) => !/^[a-z0-9.]{1,160}$/.test(path)
      || !Number.isInteger(index) || index < 0 || index > 1000)) return null;
    return { scrollTop: position.scrollTop, headingKey: position.headingKey, headingOffset: position.headingOffset, tabs: Object.fromEntries(entries) };
  } catch {
    return null;
  }
}

/** 同标签页内保留阅读位置；只保存坐标和标签页索引，不复制文档正文。 */
export function writeDocumentReadingPosition(identity: string | null, documentId: string, version: number, position: DocumentReadingPosition): void {
  const key = storageKey(identity, documentId, version);
  if (!key || typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify(position));
  } catch {
    // 存储受限时仍能正常阅读与使用目录。
  }
}
