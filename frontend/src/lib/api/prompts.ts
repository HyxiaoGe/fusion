import { API_CONFIG } from '../config';
import { apiRequest } from './fetchWithAuth';
import type { Entitlements } from './user';

const API_BASE_URL = API_CONFIG.BASE_URL;

export interface PromptExample {
  question: string;
  /** 功能目录里的分类，如 image / weather / news */
  category: string;
  /** 需要的功能开关（对应权益 features 的键）；不需要额外功能时为 null */
  requires?: keyof Entitlements['features'] | null;
}

interface PromptExamplesResponse {
  examples: PromptExample[];
  refreshed_at: string | null;
}

export async function fetchPromptExamples(limit: number = 8): Promise<PromptExamplesResponse> {
  return apiRequest<PromptExamplesResponse>(`${API_BASE_URL}/api/prompts/examples?limit=${limit}`);
}
