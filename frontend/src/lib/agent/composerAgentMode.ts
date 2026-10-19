import type { ModelCapability } from '@/lib/config/modelConfig';
import type {
  AgentPlanMode,
  AgentRunConfig,
  ComposerAgentMode,
} from '@/types/agentRun';

// 新对话页还没有对话 id，执行模式的选择先记在这个键下，首条消息发出后转给新对话。
export const NEW_CHAT_AGENT_MODE_KEY = '__new_chat__';

interface ComposerAgentModeAvailability {
  enabled: boolean;
  unavailableReason?: string;
}

interface ComposerAgentModeResolution {
  requestedMode: ComposerAgentMode;
  effectiveMode: ComposerAgentMode;
  planMode: Extract<AgentPlanMode, 'auto' | 'on'>;
  fallbackReason?: string;
}

export const COMPOSER_AGENT_MODE_LABELS: Record<ComposerAgentMode, string> = {
  auto: '自动',
  plan: '规划执行',
};

export function getComposerAgentModeAvailability(
  mode: ComposerAgentMode,
  capabilities: ModelCapability | null | undefined,
): ComposerAgentModeAvailability {
  if (mode === 'auto') {
    return { enabled: true };
  }

  if (!capabilities?.functionCalling) {
    return { enabled: false, unavailableReason: '当前模型不支持工具调用' };
  }

  return { enabled: true };
}

export function resolveComposerAgentMode(
  requestedMode: ComposerAgentMode,
  capabilities: ModelCapability | null | undefined,
): ComposerAgentModeResolution {
  const availability = getComposerAgentModeAvailability(requestedMode, capabilities);
  if (!availability.enabled) {
    return {
      requestedMode,
      effectiveMode: 'auto',
      planMode: 'auto',
      fallbackReason: availability.unavailableReason,
    };
  }

  return {
    requestedMode,
    effectiveMode: requestedMode,
    planMode: requestedMode === 'plan' ? 'on' : 'auto',
  };
}

/**
 * 由一次运行的配置还原用户当时选的执行模式：计划模式只在用户选择“规划执行”时
 * 记为 plan_mode=on，自动模式记为 off。
 */
export function composerAgentModeFromRunConfig(
  config: Pick<AgentRunConfig, 'planMode'>,
): ComposerAgentMode {
  return config.planMode === 'on' ? 'plan' : 'auto';
}
