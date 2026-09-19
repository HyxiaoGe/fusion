import type { Middleware } from '@reduxjs/toolkit';
import { selectStableAuthIdentity } from '@/lib/auth/authIdentity';
import { resetConversationListForAuthChange } from '@/redux/slices/conversationSlice';
import {
  invalidateAllConversationDetails,
  invalidateConversationDetail,
} from '@/lib/chat/conversationDetailResource';
import { invalidateAllConversationFiles } from '@/lib/chat/conversationFilesResource';
import { resetKnowledgeBaseCatalogResource } from '@/lib/chat/knowledgeBaseCatalogResource';

const conversationDetailInvalidationMiddleware: Middleware = (api) => (next) => (action) => {
  const typedAction = action as { type?: string; payload?: unknown };
  const previousAuthIdentity = selectStableAuthIdentity(api.getState());
  if (typedAction.type === 'conversation/resetConversationState') {
    invalidateAllConversationDetails();
    invalidateAllConversationFiles();
    resetKnowledgeBaseCatalogResource();
  } else if (
    typedAction.type === 'conversation/clearConversationMessages' &&
    typeof typedAction.payload === 'string'
  ) {
    invalidateConversationDetail(typedAction.payload);
  }

  const result = next(action);
  const nextAuthIdentity = selectStableAuthIdentity(api.getState());
  if (
    typedAction.type === 'auth/logout' ||
    previousAuthIdentity !== nextAuthIdentity
  ) {
    invalidateAllConversationDetails();
    invalidateAllConversationFiles();
    resetKnowledgeBaseCatalogResource();
    api.dispatch(resetConversationListForAuthChange());
    // 换账号要清掉所有会话的流槽位。此前全局只有一条流，endStream 就够了；
    // 槽位按会话拆开后没有"那一条"可以单独结束，要用全量重置。
    api.dispatch({ type: 'stream/resetStreamState' });
    api.dispatch({ type: 'fileUpload/resetFileUploadState' });
  }

  return result;
};

export default conversationDetailInvalidationMiddleware;
