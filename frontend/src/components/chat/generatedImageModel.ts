import type { AgentRunState } from '@/types/agentRun';
import type { GeneratedImageBlock } from '@/types/conversation';

const GENERATE_IMAGE_TOOL_NAME = 'generate_image';

const MAX_DISPLAY_WIDTH = 640;
const MAX_DISPLAY_HEIGHT = 560;

// 与 image-service 的 SUPPORTED_MODELS 对齐；表里没有的模型原样显示 ID。
const MODEL_DISPLAY_NAMES: Record<string, string> = {
  'doubao-seedream-5-0-pro': '豆包 Seedream 5.0 Pro',
  'doubao-seedream-5-0-flash': '豆包 Seedream 5.0 Flash',
  'doubao-seedream-5-0-lite': '豆包 Seedream 5.0 Lite',
  'doubao-seedream-4-5': '豆包 Seedream 4.5',
  'doubao-seedream-4-0': '豆包 Seedream 4.0',
  'gemini-nano-banana-2.1': 'Nano Banana 2.1',
  'nano-banana-pro-preview': 'Nano Banana Pro',
  'gemini-3.1-flash-image': 'Gemini 3.1 Flash Image',
  'gemini-3.1-flash-image-preview': 'Gemini 3.1 Flash Image',
  'gemini-3-pro-image': 'Gemini 3 Pro Image',
  'gemini-3-pro-image-preview': 'Gemini 3 Pro Image',
  'gemini-2.5-flash-image': 'Gemini 2.5 Flash Image',
  'gemini-2.5-flash-image-preview': 'Gemini 2.5 Flash Image',
  'qwen-image-3.0-pro': '通义 Qwen-Image 3.0 Pro',
  'qwen-image-3.0': '通义 Qwen-Image 3.0',
};

export type PendingImageStatus = 'running' | 'failed' | 'interrupted';

export interface PendingImageGeneration {
  toolCallId: string;
  status: PendingImageStatus;
  aspectRatio: string;
  startedAt: number;
  completedAt?: number;
  errorCode: string | null;
}

export function imageModelDisplayName(model: string | null | undefined): string | null {
  const id = model?.trim();
  if (!id) return null;
  return MODEL_DISPLAY_NAMES[id] ?? id;
}

/** 解析 "2:3" 这类比例；解析不了按 1:1。 */
export function parseAspectRatio(value: string | null | undefined): [number, number] {
  const match = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(value?.trim() ?? '');
  if (!match) return [1, 1];
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 ? [width, height] : [1, 1];
}

/** 卡片按图片比例定宽：竖图受高度限制，横图受宽度限制，窄屏再由 max-width 收缩。 */
export function imageDisplayWidth(width: number, height: number): number {
  const scale = Math.min(MAX_DISPLAY_WIDTH / width, MAX_DISPLAY_HEIGHT / height);
  return Math.round(width * scale);
}

/**
 * 还没出图的生图调用：运行中的显示生成占位；失败/中断的就地说明。
 * 成功但图片块尚未到达的调用继续占位，按 file_id 对上图片块后让位。
 */
export function collectPendingImageGenerations(
  run: AgentRunState | null | undefined,
  blocks: GeneratedImageBlock[],
): PendingImageGeneration[] {
  if (!run) return [];
  const renderedFileIds = new Set(blocks.map(block => block.file_id));
  const calls = run.steps.flatMap(step => step.toolCalls.filter(call => call.toolName === GENERATE_IMAGE_TOOL_NAME));
  const pending: PendingImageGeneration[] = [];
  calls.forEach((call, index) => {
    const summary = call.resultSummary as (Record<string, unknown> | undefined);
    const fileId = typeof summary?.file_id === 'string' ? summary.file_id : null;
    let status: PendingImageStatus;
    if (call.status === 'running') {
      status = 'running';
    } else if (call.status === 'success') {
      if (!fileId || renderedFileIds.has(fileId)) return;
      status = 'running';
    } else if (index < calls.length - 1) {
      // 失败或中断后又发起了生图（重试），由后一张卡片接替，不再保留失败卡片。
      return;
    } else if (call.status === 'interrupted') {
      status = 'interrupted';
    } else {
      status = 'failed';
    }
    const aspectRatio = typeof call.arguments?.aspect_ratio === 'string' ? call.arguments.aspect_ratio : '1:1';
    pending.push({
      toolCallId: call.toolCallId,
      status,
      aspectRatio,
      startedAt: call.startedAt,
      completedAt: call.completedAt,
      errorCode: typeof summary?.error_code === 'string' ? summary.error_code : null,
    });
  });
  return pending;
}
