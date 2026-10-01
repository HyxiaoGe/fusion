export type DocumentSourceKind =
  | 'weather'
  | 'place'
  | 'route'
  | 'flight'
  | 'train'
  | 'itinerary'
  | 'web'
  | 'url';

/** 系统按实际工具调用生成的数据来源，模型不能写入。 */
export interface DocumentSource {
  kind: DocumentSourceKind;
  label: string;
  provider?: string | null;
  url?: string | null;
  fetched_at?: string | null;
}

export interface DocumentVersionSummary {
  version: number;
  title: string;
  change_summary: string | null;
  char_count: number;
  created_at: string | null;
}

export interface DocumentDetail {
  id: string;
  conversation_id: string;
  title: string;
  format: 'markdown';
  current_version: number;
  versions: DocumentVersionSummary[];
  created_at: string | null;
  updated_at: string | null;
}

export interface DocumentVersionContent {
  document_id: string;
  version: number;
  title: string;
  format: 'markdown';
  content: string;
  change_summary: string | null;
  sources: DocumentSource[];
  created_at: string | null;
}
