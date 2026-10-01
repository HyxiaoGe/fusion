import { API_CONFIG } from '@/lib/config';
import type { DocumentDetail, DocumentVersionContent } from '@/types/document';

import { apiRequest } from './fetchWithAuth';

const BASE_PATH = `${API_CONFIG.BASE_URL}/api/documents`;

/** 读取文档元信息与全部版本摘要。 */
export function getDocument(documentId: string, signal?: AbortSignal): Promise<DocumentDetail> {
  return apiRequest<DocumentDetail>(
    `${BASE_PATH}/${encodeURIComponent(documentId)}`,
    signal ? { signal } : {},
  );
}

/** 读取指定版本正文与系统数据来源；不传版本时读取当前版本。 */
export function getDocumentContent(
  documentId: string,
  version?: number,
  signal?: AbortSignal,
): Promise<DocumentVersionContent> {
  const query = version ? `?version=${encodeURIComponent(String(version))}` : '';
  return apiRequest<DocumentVersionContent>(
    `${BASE_PATH}/${encodeURIComponent(documentId)}/content${query}`,
    signal ? { signal } : {},
  );
}
