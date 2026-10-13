
import { selectStableAuthIdentity } from '@/lib/auth/authIdentity';
import type { RootState } from './store';

// ==================== 认证 ====================

/** 当前用户是否已登录 */
export const selectIsAuthenticated = (state: RootState) => state.auth.isAuthenticated;

/** 当前认证会话的稳定身份键，避免用户资料水合时误判为切换账号；游客为 guest:<id> */
export const selectAuthSessionKey = (state: RootState) => selectStableAuthIdentity(state);

/** 未登录、以游客身份使用 */
export const selectIsGuest = (state: RootState) =>
  !state.auth.isAuthenticated && state.guest?.status === 'ready';

/** 能否聊天：已登录或已领取游客身份 */
export const selectCanChat = (state: RootState) => state.auth.isAuthenticated || selectIsGuest(state);

/** 当前用户权益；尚未拉到时为 null */
export const selectEntitlements = (state: RootState) => state.guest?.entitlements ?? null;

/** 游客今日剩余可发送消息数；非游客或额度未知时为 null */
export const selectGuestMessagesRemaining = (state: RootState) =>
  selectIsGuest(state) ? state.guest?.entitlements?.quota?.messages.remaining ?? null : null;

/**
 * 获取对话关联的模型对象。
 * 优先用对话自身的 model_id，无对话时回退到全局 selectedModelId。
 *
 * 用法：
 *   const model = useAppSelector(state => selectChatModel(state, chatId));
 */
export const selectChatModel = (state: RootState, chatId: string | null | undefined) => {
  const models = state.models.models;
  const chat = chatId ? state.conversation.byId[chatId] : undefined;
  const modelId = chat?.model_id ?? state.models.selectedModelId;
  return modelId ? models.find((m) => m.id === modelId) ?? null : null;
};
