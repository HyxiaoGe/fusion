import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';
import { ensureGuestSession, readGuestSession, type GuestSessionResult } from '@/lib/auth/guestSession';
import { fetchEntitlementsAPI, type Entitlements } from '@/lib/api/user';

/**
 * 游客身份与当前用户权益。
 *
 * - status：未登录时是否已拿到游客身份。unavailable 表示游客模式未开放或领取失败，
 *   页面回到原来的「必须登录」流程。
 * - entitlements：服务端返回的权益与今日额度，游客和登录用户都会拉取。
 */
export type GuestStatus = 'idle' | 'loading' | 'ready' | 'unavailable';

interface GuestState {
  status: GuestStatus;
  userId: string | null;
  entitlements: Entitlements | null;
}

// 和登录态一样在模块加载时读取本地游客会话：刷新后身份从第一帧起就是同一个游客，
// 不会先空再变，否则身份切换会清掉正在加载的会话详情。
function getInitialGuestState(): GuestState {
  const session = readGuestSession();
  return {
    status: session ? 'ready' : 'idle',
    userId: session?.userId ?? null,
    entitlements: null,
  };
}

const initialState: GuestState = getInitialGuestState();

export const bootstrapGuestSession = createAsyncThunk<GuestSessionResult>(
  'guest/bootstrap',
  async () => ensureGuestSession(),
);

export const refreshEntitlements = createAsyncThunk<Entitlements>(
  'guest/refreshEntitlements',
  async () => fetchEntitlementsAPI(),
);

const guestSlice = createSlice({
  name: 'guest',
  initialState,
  reducers: {
    /** 游客令牌被服务端拒绝（fetchWithAuth 已清本地会话）：回到待领取状态。 */
    guestSessionInvalidated(state) {
      state.status = 'idle';
      state.userId = null;
      state.entitlements = null;
    },
    entitlementsCleared(state) {
      state.entitlements = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(bootstrapGuestSession.pending, (state) => {
        // 已有游客身份时再次确认不退回 loading，避免身份短暂变空。
        if (state.status !== 'ready') state.status = 'loading';
      })
      .addCase(bootstrapGuestSession.fulfilled, (state, action) => {
        if (action.payload.status === 'ready') {
          state.status = 'ready';
          state.userId = action.payload.session.userId;
        } else {
          state.status = 'unavailable';
          state.userId = null;
        }
      })
      .addCase(bootstrapGuestSession.rejected, (state) => {
        state.status = 'unavailable';
        state.userId = null;
      })
      .addCase(refreshEntitlements.fulfilled, (state, action) => {
        state.entitlements = action.payload;
      });
  },
});

/** bootstrapGuestSession 的结果是否已拿到游客身份。 */
export function isGuestSessionReady(action: unknown): boolean {
  return bootstrapGuestSession.fulfilled.match(action) && action.payload.status === 'ready';
}

export const { guestSessionInvalidated, entitlementsCleared } = guestSlice.actions;
export default guestSlice.reducer;
