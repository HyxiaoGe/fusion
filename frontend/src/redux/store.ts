import { configureStore } from '@reduxjs/toolkit';
import { setupListeners } from '@reduxjs/toolkit/query';
import toastMiddleware from './middleware/toastMiddleware';
import conversationDetailInvalidationMiddleware from './middleware/conversationDetailInvalidationMiddleware';
import conversationReducer from './slices/conversationSlice';
import fileUploadReducer from './slices/fileUploadSlice';
import modelsReducer from './slices/modelsSlice';
import promptTemplatesReducer from './slices/promptTemplatesSlice';
import settingsReducer from './slices/settingsSlice';
import streamReducer from './slices/streamSlice';
import themeReducer from './slices/themeSlice';
import authReducer from './slices/authSlice';
import trajectoryReducer from './slices/trajectorySlice';
import guestReducer from './slices/guestSlice';

export const store = configureStore({
    reducer: {
        theme: themeReducer,
        conversation: conversationReducer,
        stream: streamReducer,
        models: modelsReducer,
        fileUpload: fileUploadReducer,
        promptTemplates: promptTemplatesReducer,
        settings: settingsReducer,
        auth: authReducer,
        guest: guestReducer,
        trajectory: trajectoryReducer,
    },
    middleware: (getDefaultMiddleware) => 
        getDefaultMiddleware({
            serializableCheck: false, // 在这里我们允许非序列化值，因为文件对象等可能不会被序列化
        }).concat(
            toastMiddleware,
            conversationDetailInvalidationMiddleware,
        ),
});

setupListeners(store.dispatch);

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
