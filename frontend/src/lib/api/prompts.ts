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

export interface ShowcaseImage {
  id: string;
  /** 卡片角标：这张图代表的风格或能力 */
  label: string;
  /** 点击后填入输入框的提示词，用户可改了再发 */
  prompt: string;
  /** 卡片按 4:5 裁切；主体不在中间的图指定保留区域 */
  object_position: 'top' | 'bottom' | null;
  /** 从管理员生成图挑来时的原文件 */
  source_file_id: string | null;
  image_url: string;
  /** 点开卡片查看细节的大图（长边 1600px） */
  full_image_url: string;
  width: number;
  height: number;
}

interface ShowcaseImagesResponse {
  images: ShowcaseImage[];
}

/** 首页生图示例卡片，管理员在管理中心维护 */
export async function fetchShowcaseImages(): Promise<ShowcaseImagesResponse> {
  return apiRequest<ShowcaseImagesResponse>(`${API_BASE_URL}/api/prompts/showcase`);
}

export async function fetchAdminShowcaseImages(): Promise<ShowcaseImagesResponse> {
  return apiRequest<ShowcaseImagesResponse>(`${API_BASE_URL}/api/admin/home-showcase`);
}

/** 当前管理员自己生成过的图，供挑进图片池 */
export interface GeneratedShowcaseCandidate {
  file_id: string;
  /** 生成这张图时的提示词 */
  prompt: string;
  /** 模型生成时起的简短名称，旧图没有 */
  title: string | null;
  width: number | null;
  height: number | null;
  thumbnail_url: string;
  in_pool: boolean;
}

/** 一张图和由它编辑、重画出来的各个版本，新的在前 */
export interface GeneratedShowcaseGroup {
  /** 链上最早那张图的 file_id，不随新版本或筛选变化 */
  key: string;
  versions: GeneratedShowcaseCandidate[];
}

export interface GeneratedShowcaseCandidatesPage {
  groups: GeneratedShowcaseGroup[];
  /** 下一页的起点，没有更多时为 null */
  next_offset: number | null;
}

export async function fetchGeneratedShowcaseCandidates(options: {
  offset?: number;
  hideInPool?: boolean;
} = {}): Promise<GeneratedShowcaseCandidatesPage> {
  const params = new URLSearchParams();
  if (options.offset) params.set('offset', String(options.offset));
  if (options.hideInPool) params.set('hide_in_pool', 'true');
  const query = params.toString() ? `?${params.toString()}` : '';
  return apiRequest<GeneratedShowcaseCandidatesPage>(`${API_BASE_URL}/api/admin/home-showcase/generated${query}`);
}

export async function addShowcaseFromGenerated(input: {
  fileId: string;
  label: string;
  prompt: string;
  objectPosition: 'top' | 'bottom' | null;
}): Promise<ShowcaseImage> {
  return apiRequest<ShowcaseImage>(`${API_BASE_URL}/api/admin/home-showcase/from-generated`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      file_id: input.fileId,
      label: input.label,
      prompt: input.prompt,
      object_position: input.objectPosition,
    }),
  });
}

export async function deleteShowcaseImage(id: string): Promise<void> {
  await apiRequest(`${API_BASE_URL}/api/admin/home-showcase/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
