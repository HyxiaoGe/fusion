import { createSlice, PayloadAction, createSelector } from '@reduxjs/toolkit';
import { RootState } from '../store';
import { accountSessionSwitchStarted } from '@/redux/actions/authSessionActions';

// 文件处理状态
export type FileProcessingStatus = 'pending' | 'uploading' | 'parsing' | 'processed' | 'error';

interface FileUploadState {
  fileIds: { [chatId: string]: string[] }; // 存储每个对话的文件ID列表
  processingFiles: { [fileId: string]: FileProcessingStatus };
}

const initialState: FileUploadState = {
  fileIds: {},
  processingFiles: {},
};

const fileUploadSlice = createSlice({
  name: 'fileUpload',
  initialState,
  reducers: {
    addFileId: (
      state,
      action: PayloadAction<{ chatId: string; fileId: string; fileIndex: number }>
    ) => {
      const { chatId, fileId } = action.payload;
      if (!state.fileIds[chatId]) {
        state.fileIds[chatId] = [];
      }
      state.fileIds[chatId].push(fileId);
    },
    updateFileStatus: (
      state,
      action: PayloadAction<{
        fileId: string;
        status: FileProcessingStatus;
        errorMessage?: string;
        chatId?: string;
      }>
    ) => {
      state.processingFiles[action.payload.fileId] = action.payload.status;
    },
    removeFileId: (
      state,
      action: PayloadAction<{ chatId: string; fileId: string }>
    ) => {
      const { chatId, fileId } = action.payload;
      if (state.fileIds[chatId]) {
        state.fileIds[chatId] = state.fileIds[chatId].filter(id => id !== fileId);
      }
      delete state.processingFiles[fileId];
    },
    clearFiles: (state, action: PayloadAction<string>) => {
      const chatId = action.payload;
      state.fileIds[chatId]?.forEach((fileId) => {
        delete state.processingFiles[fileId];
      });
      delete state.fileIds[chatId];
    },
    // 重置所有文件上传状态（用于退出登录）
    resetFileUploadState: () => initialState,
  },
  extraReducers: (builder) => {
    builder.addCase(accountSessionSwitchStarted, () => initialState);
  },
});

export const {
  clearFiles,
  addFileId,
  removeFileId,
  updateFileStatus,
  resetFileUploadState,
} = fileUploadSlice.actions;

// --- Memoized Selectors --- 

const EMPTY_ARRAY: any[] = [];
const selectFileIdsByChatState = (state: RootState) => state.fileUpload.fileIds;

export const makeSelectChatFileIds = () => createSelector(
  [selectFileIdsByChatState, (_state: RootState, chatId: string) => chatId],
  (fileIdsByChat, chatId) => fileIdsByChat[chatId] || EMPTY_ARRAY // Return stable empty array
);

export default fileUploadSlice.reducer;
