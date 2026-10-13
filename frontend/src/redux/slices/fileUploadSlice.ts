import { FileWithPreview, revokeFilePreview } from '@/lib/utils/fileHelpers';
import { createSlice, PayloadAction, createSelector } from '@reduxjs/toolkit';
import { RootState } from '../store';
import { accountSessionSwitchStarted } from '@/redux/actions/authSessionActions';

// 文件处理状态
export type FileProcessingStatus = 'pending' | 'uploading' | 'parsing' | 'processed' | 'error';

interface FileWithId extends FileWithPreview {
  fileId?: string; // 服务器返回的文件ID
  status?: FileProcessingStatus; // 文件处理状态
  errorMessage?: string; // 错误信息
}

interface FileUploadState {
  files: { [chatId: string]: FileWithId[] };
  fileIds: { [chatId: string]: string[] }; // 存储每个对话的文件ID列表
  isUploading: boolean;
  uploadProgress: number;
  error: string | null;
  processingFiles: { [fileId: string]: FileProcessingStatus };
}

const initialState: FileUploadState = {
  files: {},
  fileIds: {},
  isUploading: false,
  uploadProgress: 0,
  error: null,
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
      const { chatId, fileId, fileIndex } = action.payload;
      if (!state.fileIds[chatId]) {
        state.fileIds[chatId] = [];
      }
      state.fileIds[chatId].push(fileId);
      
      // 同时更新对应文件对象中的fileId属性
      if (state.files[chatId] && state.files[chatId][fileIndex]) {
        state.files[chatId][fileIndex].fileId = fileId;
        // 上传成功后，将状态设置为parsing
        state.files[chatId][fileIndex].status = 'parsing';
        // 添加到正在处理的文件列表
        state.processingFiles[fileId] = 'parsing';
      }
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
      const { fileId, status, errorMessage, chatId } = action.payload;
      
      // 更新全局处理状态跟踪
      state.processingFiles[fileId] = status;
      
      // 如果提供了chatId，则更新相应聊天中的文件状态
      if (chatId && state.files[chatId]) {
        const fileIndex = state.files[chatId].findIndex(file => file.fileId === fileId);
        if (fileIndex !== -1) {
          state.files[chatId][fileIndex].status = status;
          if (errorMessage) {
            state.files[chatId][fileIndex].errorMessage = errorMessage;
          }
        }
      } else {
        // 如果没有提供chatId，尝试在所有聊天中查找该文件
        Object.keys(state.files).forEach(chatKey => {
          const fileIndex = state.files[chatKey].findIndex(file => file.fileId === fileId);
          if (fileIndex !== -1) {
            state.files[chatKey][fileIndex].status = status;
            if (errorMessage) {
              state.files[chatKey][fileIndex].errorMessage = errorMessage;
            }
          }
        });
      }
    },
    removeFileId: (
      state,
      action: PayloadAction<{ chatId: string; fileId: string }>
    ) => {
      const { chatId, fileId } = action.payload;
      if (state.fileIds[chatId]) {
        state.fileIds[chatId] = state.fileIds[chatId].filter(id => id !== fileId);
      }

      // 同时从处理状态跟踪中移除
      if (state.processingFiles[fileId]) {
        delete state.processingFiles[fileId];
      }
    },
    clearFiles: (state, action: PayloadAction<string>) => {
      const chatId = action.payload;
      if (state.files[chatId]) {
        // 释放所有预览URL
        state.files[chatId].forEach(file => {
          revokeFilePreview(file);
          // 如果有fileId，从处理状态跟踪中移除
          if (file.fileId) {
            delete state.processingFiles[file.fileId];
          }
        });
        delete state.files[chatId];
      }
      // 清除该聊天的文件ID列表
      if (state.fileIds[chatId]) {
        delete state.fileIds[chatId];
      }
    },
    // 重置所有文件上传状态（用于退出登录）
    resetFileUploadState: (state) => {
      // 释放所有预览URL
      Object.keys(state.files).forEach(chatId => {
        state.files[chatId].forEach(file => {
          revokeFilePreview(file);
        });
      });
      
      // 重置为初始状态
      state.files = {};
      state.fileIds = {};
      state.isUploading = false;
      state.uploadProgress = 0;
      state.error = null;
      state.processingFiles = {};
    },
  },
  extraReducers: (builder) => {
    builder.addCase(accountSessionSwitchStarted, (state) => {
      Object.values(state.files).flat().forEach(revokeFilePreview);
      Object.assign(state, initialState);
    });
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
