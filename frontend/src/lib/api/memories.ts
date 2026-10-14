import { API_CONFIG } from '@/lib/config';
import { apiRequest } from './fetchWithAuth';

export interface MemoryItem {
  id: string;
  category: string;
  content: string;
  source_conversation_id: string | null;
  updated_at: string;
}

export interface MemoryOverview {
  enabled: boolean;
  items: MemoryItem[];
}

const memoriesPath = `${API_CONFIG.BASE_URL}/api/memories`;

export function getMemories(signal?: AbortSignal): Promise<MemoryOverview> {
  return apiRequest<MemoryOverview>(memoriesPath, { signal });
}

export function setMemoryEnabled(enabled: boolean): Promise<{ enabled: boolean }> {
  return apiRequest<{ enabled: boolean }>(`${memoriesPath}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
}

export function deleteMemory(id: string): Promise<{ deleted: boolean }> {
  return apiRequest<{ deleted: boolean }>(`${memoriesPath}/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function clearMemories(): Promise<{ deleted: number }> {
  return apiRequest<{ deleted: number }>(memoriesPath, { method: 'DELETE' });
}
