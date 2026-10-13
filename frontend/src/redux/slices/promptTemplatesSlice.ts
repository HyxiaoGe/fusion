import {
  getAllPromptTemplates,
  initializeDefaultPromptTemplates,
  PromptTemplate,
} from '@/lib/db/promptTemplates';
import { createAsyncThunk, createSlice, PayloadAction } from '@reduxjs/toolkit';
import { accountSessionSwitchStarted } from '@/redux/actions/authSessionActions';

interface PromptTemplatesState {
  templates: PromptTemplate[];
  selectedTemplateId: number | null;
  categories: string[];
  isLoading: boolean;
  error: string | null;
}

const initialState: PromptTemplatesState = {
  templates: [],
  selectedTemplateId: null,
  categories: ['编程', '写作', '学习', '其他'],
  isLoading: false,
  error: null,
};

// 初始化提示词模板
export const initializeTemplates = createAsyncThunk(
  'promptTemplates/initialize',
  async (_, { rejectWithValue }) => {
    try {
      await initializeDefaultPromptTemplates();
      const templates = await getAllPromptTemplates();
      return templates;
    } catch {
      return rejectWithValue('无法初始化提示词模板');
    }
  }
);

const promptTemplatesSlice = createSlice({
  name: 'promptTemplates',
  initialState,
  reducers: {
    setSelectedTemplate: (state, action: PayloadAction<number | null>) => {
      state.selectedTemplateId = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(accountSessionSwitchStarted, () => initialState)
      // 初始化模板
      .addCase(initializeTemplates.pending, (state) => {
        state.isLoading = true;
      })
      .addCase(initializeTemplates.fulfilled, (state, action) => {
        state.templates = action.payload;
        state.isLoading = false;
        state.error = null;
      })
      .addCase(initializeTemplates.rejected, (state, action) => {
        state.isLoading = false;
        state.error = action.payload as string;
      });
  },
});

export const { setSelectedTemplate } = promptTemplatesSlice.actions;

export default promptTemplatesSlice.reducer;
