import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';
import { ensureGuestSession, type GuestSessionResult } from '@/lib/auth/guestSession';
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

const initialState: GuestState = {
  status: 'idle',
  userId: null,
  entitlements: null,
};

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
        state.status = 'loading';
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
