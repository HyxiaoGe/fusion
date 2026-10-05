import { AlertCircle, Brain, ListChecks, Sparkles, Square } from 'lucide-react';
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
    return { key: 'failed', tone: 'danger', icon: AlertCircle, title: '生成失败', detail: '请重试', busy: false, role: 'alert' };
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
