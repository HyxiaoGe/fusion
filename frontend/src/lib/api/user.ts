import { API_CONFIG } from '../config';
import { apiRequest } from './fetchWithAuth';

export interface UserProfile {
  id: string;
  username: string;
  email: string | null;
  nickname: string | null;
  avatar: string | null;
  mobile: string | null;
  loginProvider?: 'github' | 'google' | null;
  system_prompt: string;
  is_superuser: boolean;
}

export const fetchUserProfileAPI = async (): Promise<UserProfile> => {
  return apiRequest<UserProfile>(`${API_CONFIG.BASE_URL}/api/auth/me`);
};

export const updateUserSettingsAPI = async (
  systemPrompt: string,
): Promise<{ system_prompt: string }> => {
  return apiRequest<{ system_prompt: string }>(`${API_CONFIG.BASE_URL}/api/auth/me`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: systemPrompt }),
  });
};

export interface UsageQuota {
  limit: number;
  used: number;
  remaining: number;
}

export interface Entitlements {
  tier: 'guest' | 'registered';
  features: {
    model_selection: boolean;
    file_upload: boolean;
    image_generation: boolean;
    map: boolean;
    knowledge_base: boolean;
    mcp_tools: boolean;
    personalization: boolean;
  };
  /** 游客可用的模型；登录用户为 null（不限）。 */
  models: string[] | null;
  /** 游客今日额度；登录用户或额度服务不可用时为 null。 */
  quota: { messages: UsageQuota; searches: UsageQuota } | null;
}

export const fetchEntitlementsAPI = async (): Promise<Entitlements> => {
  return apiRequest<Entitlements>(`${API_CONFIG.BASE_URL}/api/auth/me/entitlements`);
};
