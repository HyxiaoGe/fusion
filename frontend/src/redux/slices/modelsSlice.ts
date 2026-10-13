import { ModelInfo, ProviderInfo } from "@/lib/config/modelConfig";
import { getPreferredModelId } from "@/lib/models/modelPreference";
import { createSlice, PayloadAction } from "@reduxjs/toolkit";

// 从localStorage获取之前选择的模型ID
const getSavedModelId = (): string | null => {
  if (typeof window !== 'undefined') {
    try {
      const savedModelId = localStorage.getItem('selectedModelId');
      return savedModelId;
    } catch (error) {
      console.error('Error loading selectedModelId from localStorage:', error);
    }
  }
  return null;
};

// 原有接口保持兼容
export type Model = ModelInfo;

type ModelsLoadStatus = 'idle' | 'loading' | 'ready' | 'failed';

interface ModelsState {
  models: Model[];
  providers: ProviderInfo[];
  selectedModelId: string | null;
  isLoading: boolean;
  loadStatus: ModelsLoadStatus;
}

const initialState: ModelsState = {
  models: [],
  providers: [],
  selectedModelId: getSavedModelId(),
  isLoading: false,
  loadStatus: 'idle',
};

const modelsSlice = createSlice({
  name: 'models',
  initialState,
  reducers: {
    setSelectedModel: (state, action: PayloadAction<string>) => {
      state.selectedModelId = action.payload;
      // 保存到localStorage
      if (typeof window !== 'undefined') {
        try {
          localStorage.setItem('selectedModelId', action.payload);
        } catch (error) {
          console.error('Error saving selectedModelId to localStorage:', error);
        }
      }
    },
    setModelsLoadStatus: (state, action: PayloadAction<ModelsLoadStatus>) => {
      state.loadStatus = action.payload;
      state.isLoading = action.payload === 'loading';
    },
    updateProviders: (state, action: PayloadAction<ProviderInfo[]>) => {
      state.providers = action.payload;
    },
    updateModels: (state, action: PayloadAction<Model[]>) => {
      state.models = action.payload;
      state.loadStatus = 'ready';
      state.isLoading = false;
      
      // 获取保存的模型ID
      const savedModelId = getSavedModelId();

      const preferredModelId = getPreferredModelId(
        action.payload,
        savedModelId || state.selectedModelId,
      );

      if (preferredModelId !== state.selectedModelId) {
        state.selectedModelId = preferredModelId;
      }

      if (state.selectedModelId && typeof window !== 'undefined') {
        try {
          localStorage.setItem('selectedModelId', state.selectedModelId);
        } catch (error) {
          console.error('Error saving selectedModelId to localStorage:', error);
        }
      }
    }
  }
});

export const {
  setSelectedModel,
  setModelsLoadStatus,
  updateProviders,
  updateModels
} = modelsSlice.actions;
export default modelsSlice.reducer;
