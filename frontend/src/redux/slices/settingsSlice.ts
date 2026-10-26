import { createSlice, PayloadAction } from '@reduxjs/toolkit';

interface SettingsState {
  // 弹窗相关状态
  isSettingsDialogOpen: boolean;
  activeSettingsTab: string;
  // 更新日志弹窗：focusChangelogId 为从通知进入时要定位的版本
  isChangelogDialogOpen: boolean;
  focusChangelogId: string | null;
  // 消息盒子：通知面板「查看全部」打开的完整列表
  isNotificationsDialogOpen: boolean;
  // 额度面板：每次从通知请求打开时加一，输入框旁的额度入口据此展开
  creditsPanelRequest: number;
  // 其他设置项...
}

const initialState: SettingsState = {
  // 弹窗相关状态
  isSettingsDialogOpen: false,
  activeSettingsTab: 'general',
  isChangelogDialogOpen: false,
  focusChangelogId: null,
  isNotificationsDialogOpen: false,
  creditsPanelRequest: 0,
  // 其他设置的初始值...
};

const settingsSlice = createSlice({
  name: 'settings',
  initialState,
  reducers: {
    setActiveSettingsTab: (state, action: PayloadAction<string>) => {
      state.activeSettingsTab = action.payload;
    },
    openSettingsDialog: (state, action: PayloadAction<{ tab?: string }>) => {
      state.isSettingsDialogOpen = true;
      if (action.payload.tab) {
        state.activeSettingsTab = action.payload.tab;
      }
    },
    closeSettingsDialog: (state) => {
      state.isSettingsDialogOpen = false;
    },
    openChangelogDialog: (state, action: PayloadAction<{ changelogId?: string }>) => {
      state.isChangelogDialogOpen = true;
      state.focusChangelogId = action.payload.changelogId ?? null;
    },
    closeChangelogDialog: (state) => {
      state.isChangelogDialogOpen = false;
      state.focusChangelogId = null;
    },
    openNotificationsDialog: (state) => {
      state.isNotificationsDialogOpen = true;
    },
    closeNotificationsDialog: (state) => {
      state.isNotificationsDialogOpen = false;
    },
    openCreditsPanel: (state) => {
      state.creditsPanelRequest += 1;
    },
    // 其他设置的reducer...
  },
});

export const {
  setActiveSettingsTab,
  openSettingsDialog,
  closeSettingsDialog,
  openChangelogDialog,
  closeChangelogDialog,
  openNotificationsDialog,
  closeNotificationsDialog,
  openCreditsPanel,
} = settingsSlice.actions;
export default settingsSlice.reducer;