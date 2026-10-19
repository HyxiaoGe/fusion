import { AlertCircle, Brain, Hourglass, ListChecks, Sparkles, Square } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { getToolMeta, type SemanticColor } from '@/lib/agent/toolRegistry';
import type { AssistantActivity, AssistantActivityKind } from './assistantActivity';

/** 状态栏的统一视图：所有阶段都按“图标 + 标题 + 补充说明 + 进行中指示”渲染。 */
export interface ActivityStatusView {
  /** 阶段切换时用于重新挂载，触发入场过渡。 */
  key: string;
  tone: SemanticColor;
  icon: LucideIcon;
  title: string;
  detail?: string;
  busy: boolean;
  role: 'status' | 'alert';
}

type ModelPhase = Extract<AssistantActivityKind, 'preparing' | 'reasoning' | 'analyzing'>;

/** 模型自身的阶段。新增阶段只需在此补一行；Record 保证不会漏配。 */
const MODEL_PHASE_VIEWS: Record<ModelPhase, Pick<ActivityStatusView, 'tone' | 'icon' | 'title'>> = {
  preparing: { tone: 'neutral', icon: Sparkles, title: '正在准备回答' },
  // 与思考块头部（ReasoningContent）同一图标与色调、同一说法。
  reasoning: { tone: 'info', icon: Brain, title: '正在深度思考' },
  analyzing: { tone: 'neutral', icon: ListChecks, title: '正在分析结果' },
};

function isModelPhase(kind: AssistantActivityKind): kind is ModelPhase {
  return Object.prototype.hasOwnProperty.call(MODEL_PHASE_VIEWS, kind);
}

/** 这些阶段在等模型继续输出（工具执行期间没有模型输出，不算）。 */
const AWAITING_MODEL_OUTPUT_KINDS: ReadonlySet<AssistantActivityKind> = new Set([
  'preparing',
  'reasoning',
  'analyzing',
  'answering',
  'drafting',
]);

/** 模型阶段超过该秒数没有新输出，就提示用户模型变慢（后端首字 10 秒换模型、中途 30 秒截断）。 */
export const MODEL_SLOW_AFTER_S = 10;

export function isAwaitingModelOutput(kind: AssistantActivityKind): boolean {
  return AWAITING_MODEL_OUTPUT_KINDS.has(kind);
}

/** 模型长时间没有新输出时的状态：如实说明在等模型，用户可以停止。 */
export function resolveSlowModelStatus(silentS: number): ActivityStatusView {
  return {
    key: 'model_slow',
    tone: 'warn',
    icon: Hourglass,
    title: '模型响应变慢',
    detail: `已等待 ${silentS} 秒，可以停止后重试`,
    busy: true,
    role: 'status',
  };
}

export interface ResolveActivityStatusOptions {
  /** 思考块正在展示时，模型阶段由思考块自己表达，状态栏不重复。 */
  reasoningVisible?: boolean;
  /** 停止请求等待服务端确认时不展示任何生成期状态。 */
  stopPending?: boolean;
}

export function resolveActivityStatus(
  activity: AssistantActivity,
  { reasoningVisible = false, stopPending = false }: ResolveActivityStatusOptions = {},
): ActivityStatusView | null {
  if (stopPending) return null;

  if (activity.kind === 'failed') {
    return {
      key: 'failed',
      tone: 'danger',
      icon: AlertCircle,
      title: '生成失败',
      detail: activity.failureMessage ?? '请重试',
      busy: false,
      role: 'alert',
    };
  }

  if (activity.kind === 'interrupted') {
    return { key: 'interrupted', tone: 'neutral', icon: Square, title: '生成已停止', busy: false, role: 'status' };
  }

  if (activity.kind === 'tool_running' && activity.tool) {
    // 工具的图标与色调统一来自工具注册表，新工具无需改状态栏。
    const meta = getToolMeta(activity.tool.toolName);
    return {
      key: `tool:${activity.tool.call.toolCallId}`,
      tone: meta.color,
      icon: meta.icon,
      title: activity.tool.label,
      detail: activity.tool.target || undefined,
      busy: true,
      role: 'status',
    };
  }

  if (activity.kind === 'drafting' && activity.draft) {
    const meta = getToolMeta(activity.draft.toolName);
    return {
      key: `draft:${activity.draft.toolName}`,
      tone: meta.color,
      icon: meta.icon,
      title: `正在${meta.label}`,
      detail: activity.draft.title || undefined,
      busy: true,
      role: 'status',
    };
  }

  if (isModelPhase(activity.kind)) {
    if (reasoningVisible) return null;
    return { key: activity.kind, ...MODEL_PHASE_VIEWS[activity.kind], busy: true, role: 'status' };
  }

  if (activity.issue) {
    return {
      key: `issue:${activity.issue.call.toolCallId}`,
      tone: activity.issue.kind === 'failed' ? 'danger' : 'warn',
      icon: AlertCircle,
      title: activity.issue.title,
      detail: activity.issue.detail,
      busy: false,
      role: 'status',
    };
  }

  return null;
}
