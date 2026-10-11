import type { RootState } from '@/redux/store';
import type { Model } from '@/redux/slices/modelsSlice';
import { AUTO_MODEL_ID } from '@/lib/config/modelConfig';

export type SendModelResolution =
  | { status: 'ready'; model: Model; fallbackFromModelId?: string }
  | { status: 'conversation_not_ready' }
  | { status: 'conversation_model_unavailable' }
  | { status: 'no_enabled_model' };

export function isModelAvailableForSending(model: Model): boolean {
  const routable = model.routable ?? model.health?.status !== 'unhealthy';
  return model.enabled !== false && routable;
}

export function isModelAvailableForNewConversation(model: Model): boolean {
  return (
    model.selectable !== false
    && model.health?.status !== 'unhealthy'
    && isModelAvailableForSending(model)
  );
}

type GuestScopeState = Partial<Pick<RootState, 'auth' | 'guest'>>;

/**
 * 游客新会话可用的模型：自动选择 + 服务端下发的模型；非游客返回 null（不限）。
 * 权益尚未拉到时只放行自动选择，服务端也只认这几个。
 */
export function getGuestAllowedModelIds(state: GuestScopeState): ReadonlySet<string> | null {
  if (state.auth?.isAuthenticated !== false || state.guest?.status !== 'ready') return null;
  return new Set([AUTO_MODEL_ID, ...(state.guest.entitlements?.models ?? [])]);
}

export function resolveSendModel(
  state: Pick<RootState, 'conversation' | 'models'> & GuestScopeState,
  conversationId: string | null,
): SendModelResolution {
  if (conversationId !== null) {
    const conversation = state.conversation.byId[conversationId];
    const conversationModelId = conversation?.model_id;
    if (!conversationModelId) {
      return { status: 'conversation_not_ready' };
    }

    const hasUserTurn = conversation.messages?.some((message) => message.role === 'user') ?? false;
    const conversationModel = state.models.models.find(
      (model) => (
        model.id === conversationModelId
        && (
          hasUserTurn
            ? isModelAvailableForSending(model)
            : isModelAvailableForNewConversation(model)
        )
      ),
    );
    if (conversationModel) {
      return { status: 'ready', model: conversationModel };
    }
    // 已有对话的绑定模型下线后，服务端会每轮改走自动选择，前端同步按「自动选择」放行。
    const autoModel = hasUserTurn
      ? state.models.models.find((model) => model.id === AUTO_MODEL_ID && isModelAvailableForSending(model))
      : undefined;
    return autoModel
      ? { status: 'ready', model: autoModel, fallbackFromModelId: conversationModelId }
      : { status: 'conversation_model_unavailable' };
  }

  const guestAllowed = getGuestAllowedModelIds(state);
  const isSelectableHere = (model: Model) => (
    isModelAvailableForNewConversation(model) && (!guestAllowed || guestAllowed.has(model.id))
  );
  const selectedModel = state.models.models.find(
    (model) => model.id === state.models.selectedModelId && isSelectableHere(model),
  );
  const fallbackModel = selectedModel
    ?? (guestAllowed ? state.models.models.find((model) => model.id === AUTO_MODEL_ID && isSelectableHere(model)) : undefined)
    ?? state.models.models.find(isSelectableHere);
  return fallbackModel
    ? { status: 'ready', model: fallbackModel }
    : { status: 'no_enabled_model' };
}

export function getSendModelErrorMessage(
  resolution: Exclude<SendModelResolution, { status: 'ready'; model: Model }>,
): string {
  if (resolution.status === 'conversation_not_ready') {
    return '对话尚未加载完成，请稍后重试';
  }
  if (resolution.status === 'conversation_model_unavailable') {
    return '该对话使用的模型当前不可用，请新建对话并选择其他模型';
  }
  return '没有可用的模型，请先在设置中启用一个模型';
}
