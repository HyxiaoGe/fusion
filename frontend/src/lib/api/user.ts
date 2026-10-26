import { API_CONFIG } from '../config';
import type { CreditsSummary } from './credits';
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
  /** 当前日、周 credits 额度；游客和登录用户都有，管理员 unlimited。 */
  credits: CreditsSummary | null;
}

export const fetchEntitlementsAPI = async (): Promise<Entitlements> => {
  return apiRequest<Entitlements>(`${API_CONFIG.BASE_URL}/api/auth/me/entitlements`);
};
