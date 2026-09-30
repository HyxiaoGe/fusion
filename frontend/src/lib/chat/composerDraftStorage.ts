import { v4 as uuidv4 } from 'uuid';

const STORAGE_KEY_PREFIX = 'fusion:composer-draft:';

type ComposerDraftRecord = { text: string; revision: string | null };

function getDraftKey(authIdentity: string | null, conversationId: string | null): string | null {
  if (!authIdentity) return null;
  // 用元组区分新对话与已有会话，同时避免账号或会话 ID 中的分隔符碰撞。
  return `${STORAGE_KEY_PREFIX}${JSON.stringify([authIdentity, conversationId])}`;
}

function readDraft(storage: Storage, key: string): ComposerDraftRecord | null {
  const storedValue = storage.getItem(key);
  if (storedValue === null) return null;
  const parsedValue: unknown = JSON.parse(storedValue);
  if (typeof parsedValue === 'string') return { text: parsedValue, revision: null };
  if (
    typeof parsedValue === 'object' && parsedValue !== null
    && 'text' in parsedValue && typeof parsedValue.text === 'string'
    && 'revision' in parsedValue && typeof parsedValue.revision === 'string' && parsedValue.revision !== ''
  ) {
    return { text: parsedValue.text, revision: parsedValue.revision };
  }
  return null;
}

export function readComposerDraft(authIdentity: string | null, conversationId: string | null): string {
  const key = getDraftKey(authIdentity, conversationId);
  if (!key || typeof window === 'undefined') return '';
  try {
    return readDraft(window.sessionStorage, key)?.text ?? '';
  } catch {
    return '';
  }
}

export function readComposerDraftRevision(authIdentity: string | null, conversationId: string | null): string | null {
  const key = getDraftKey(authIdentity, conversationId);
  if (!key || typeof window === 'undefined') return null;
  try {
    return readDraft(window.sessionStorage, key)?.revision ?? null;
  } catch {
    return null;
  }
}

export function writeComposerDraft(authIdentity: string | null, conversationId: string | null, text: string): void {
  const key = getDraftKey(authIdentity, conversationId);
  if (!key || typeof window === 'undefined') return;
  try {
    if (text === '') {
      window.sessionStorage.removeItem(key);
    } else {
      window.sessionStorage.setItem(key, JSON.stringify({ text, revision: uuidv4() }));
    }
  } catch {
    // 存储受限时仍允许当前输入继续使用，不阻断编辑和发送。
  }
}

export function clearComposerDraftIfUnchanged(
  authIdentity: string | null,
  conversationId: string | null,
  submittedText: string,
  expectedRevision?: string | null,
): void {
  const key = getDraftKey(authIdentity, conversationId);
  if (!key || typeof window === 'undefined') return;
  try {
    const storage = window.sessionStorage;
    const draft = readDraft(storage, key);
    // 版本也参与比对，避免重置或重写相同文本后被旧发送确认清除。
    if (draft?.text === submittedText && (expectedRevision === undefined || draft.revision === expectedRevision)) {
      storage.removeItem(key);
    }
  } catch {
    // 无法读取或清除存储不影响发送成功后的界面状态。
  }
}

export function moveComposerDraft(
  authIdentity: string | null,
  fromConversationId: string | null,
  toConversationId: string | null,
): void {
  const fromKey = getDraftKey(authIdentity, fromConversationId);
  const toKey = getDraftKey(authIdentity, toConversationId);
  if (!fromKey || !toKey || fromKey === toKey || typeof window === 'undefined') return;
  try {
    const storage = window.sessionStorage;
    const sourceDraft = readDraft(storage, fromKey);
    if (sourceDraft === null) return;
    const targetDraft = readDraft(storage, toKey);
    // 会话物化时保留目标已有输入；目标为空才迁移源草稿。
    if (sourceDraft.text !== '' && !targetDraft?.text) {
      // 搬迁保留原版本；旧纯文本格式继续以兼容格式保存。
      storage.setItem(toKey, JSON.stringify(sourceDraft.revision === null ? sourceDraft.text : sourceDraft));
    }
    // 目标写入失败会进入 catch，源草稿保持不变。
    storage.removeItem(fromKey);
  } catch {
    // 草稿迁移失败不阻断会话创建，也不主动删除尚未保存的输入。
  }
}
