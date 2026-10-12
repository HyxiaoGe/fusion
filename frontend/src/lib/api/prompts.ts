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

export interface PromptExamplesResponse {
  examples: PromptExample[];
  refreshed_at: string | null;
}

export type PromptTemplateKind = 'starter' | 'template';

export interface PromptTemplateCatalogItem {
  id: string;
  kind: PromptTemplateKind;
  title: string;
  description: string;
  content: string;
  category: string;
  icon_key: string;
  tone: string;
  sort_order: number;
  enabled: boolean;
  /** 需要的功能开关（权益 features 的键），全部开启才展示 */
  required_capabilities: Array<keyof Entitlements['features']>;
}

export interface PromptTemplateCatalogResponse {
  items: PromptTemplateCatalogItem[];
  source: string;
  version: string;
}

/**
 * 获取动态示例问题（无需鉴权）
 */
export async function fetchPromptExamples(limit: number = 8): Promise<PromptExamplesResponse> {
  return apiRequest<PromptExamplesResponse>(`${API_BASE_URL}/api/prompts/examples?limit=${limit}`);
}

/**
 * 获取首页任务卡与模板目录（无需鉴权）
 */
export async function fetchPromptTemplates(): Promise<PromptTemplateCatalogResponse> {
  return apiRequest<PromptTemplateCatalogResponse>(`${API_BASE_URL}/api/prompts/templates`);
}
