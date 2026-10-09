import { API_CONFIG } from '@/lib/config';
import { apiRequest } from './fetchWithAuth';

export interface ChangelogSummary {
  id: string;
  version: string;
  title: string;
  summary: string;
  published_at: string;
}

export interface ChangelogDetail extends ChangelogSummary {
  content: string;
  notification_id: string | null;
}

export interface ChangelogPage {
  items: ChangelogDetail[];
  next_cursor: string | null;
}

const changelogsPath = `${API_CONFIG.BASE_URL}/api/changelogs`;

export function getChangelogs(params: { cursor?: string; limit?: number } = {}, signal?: AbortSignal): Promise<ChangelogPage> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 20) });
  if (params.cursor) query.set('cursor', params.cursor);
  return apiRequest<ChangelogPage>(`${changelogsPath}?${query}`, { signal });
}
