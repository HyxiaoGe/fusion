import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearComposerDraftIfUnchanged,
  moveComposerDraft,
  readComposerDraft,
  readComposerDraftRevision,
  writeComposerDraft,
} from './composerDraftStorage';

describe('composerDraftStorage', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('保存文本中的空格、换行和中文，重新加载模块后仍可恢复', async () => {
    const text = '  继续整理这段内容\n保留换行  ';
    writeComposerDraft('user-a', 'chat-a', text);

    vi.resetModules();
    const restoredModule = await import('./composerDraftStorage');
    expect(restoredModule.readComposerDraft('user-a', 'chat-a')).toBe(text);
  });

  it('按账号与会话隔离已有会话和新对话草稿', () => {
    writeComposerDraft('user-a', null, '新对话');
    writeComposerDraft('user-a', 'chat-a', '会话 A');
    writeComposerDraft('user-a', 'chat-b', '会话 B');
    writeComposerDraft('user-b', 'chat-a', '另一个账号');

    expect(readComposerDraft('user-a', null)).toBe('新对话');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('会话 A');
    expect(readComposerDraft('user-a', 'chat-b')).toBe('会话 B');
    expect(readComposerDraft('user-b', 'chat-a')).toBe('另一个账号');
    expect(readComposerDraft('user-b', null)).toBe('');
  });

  it('新对话与同名已有会话、带分隔符的标识不会碰撞', () => {
    writeComposerDraft('user:a', null, '新对话');
    writeComposerDraft('user:a', 'new', '会话 new');
    writeComposerDraft('user:a', 'chat:b', '第一组');
    writeComposerDraft('user:a:chat', 'b', '第二组');

    expect(readComposerDraft('user:a', null)).toBe('新对话');
    expect(readComposerDraft('user:a', 'new')).toBe('会话 new');
    expect(readComposerDraft('user:a', 'chat:b')).toBe('第一组');
    expect(readComposerDraft('user:a:chat', 'b')).toBe('第二组');
  });

  it('空字符串移除草稿，只有空白的文本仍然保留', () => {
    writeComposerDraft('user-a', 'chat-a', '旧草稿');
    writeComposerDraft('user-a', 'chat-a', '');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(sessionStorage.length).toBe(0);

    writeComposerDraft('user-a', 'chat-a', ' \n ');
    expect(readComposerDraft('user-a', 'chat-a')).toBe(' \n ');
  });

  it('发送确认只清除同一账号、同一会话且文本完全一致的草稿', () => {
    writeComposerDraft('user-a', 'chat-a', '已提交');
    writeComposerDraft('user-a', 'chat-b', '已提交');
    writeComposerDraft('user-b', 'chat-a', '已提交');

    clearComposerDraftIfUnchanged('user-a', 'chat-a', '已提交');

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraft('user-a', 'chat-b')).toBe('已提交');
    expect(readComposerDraft('user-b', 'chat-a')).toBe('已提交');
  });

  it('晚到发送确认保留用户已经编辑的新草稿', () => {
    writeComposerDraft('user-a', 'chat-a', '旧问题');
    writeComposerDraft('user-a', 'chat-a', '下一条问题');

    clearComposerDraftIfUnchanged('user-a', 'chat-a', '旧问题');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('下一条问题');
  });

  it('条件清除不忽略空白差异', () => {
    writeComposerDraft('user-a', 'chat-a', '问题 ');

    clearComposerDraftIfUnchanged('user-a', 'chat-a', '问题');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('问题 ');
  });

  it.each([false, true])('相同文本重新编辑后，旧确认不能清除新的草稿（先重置：%s）', (resetFirst) => {
    writeComposerDraft('user-a', 'chat-a', '重复问题');
    const submittedRevision = readComposerDraftRevision('user-a', 'chat-a');
    expect(submittedRevision).not.toBeNull();
    if (resetFirst) writeComposerDraft('user-a', 'chat-a', '');
    writeComposerDraft('user-a', 'chat-a', '重复问题');

    expect(readComposerDraftRevision('user-a', 'chat-a')).not.toBe(submittedRevision);
    clearComposerDraftIfUnchanged('user-a', 'chat-a', '重复问题', submittedRevision);

    expect(readComposerDraft('user-a', 'chat-a')).toBe('重复问题');
  });

  it('文本和提交时的版本都一致才清除草稿', () => {
    writeComposerDraft('user-a', 'chat-a', '已提交');
    const submittedRevision = readComposerDraftRevision('user-a', 'chat-a');

    clearComposerDraftIfUnchanged('user-a', 'chat-a', '已提交', submittedRevision);

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();
  });

  it('兼容旧格式纯文本记录，迁移后仍可读取且版本为空', () => {
    writeComposerDraft('user-a', null, '占位');
    const key = sessionStorage.key(0)!;
    sessionStorage.setItem(key, JSON.stringify('旧版草稿'));

    expect(readComposerDraft('user-a', null)).toBe('旧版草稿');
    expect(readComposerDraftRevision('user-a', null)).toBeNull();
    moveComposerDraft('user-a', null, 'chat-a');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('旧版草稿');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();

    clearComposerDraftIfUnchanged('user-a', 'chat-a', '旧版草稿', null);
    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
  });

  it('新会话物化时迁移后续草稿并删除源，保留其他账号草稿', () => {
    writeComposerDraft('user-a', 'temporary-chat', '下一条问题\n ');
    writeComposerDraft('user-b', 'temporary-chat', '其他账号草稿');
    const sourceRevision = readComposerDraftRevision('user-a', 'temporary-chat');

    moveComposerDraft('user-a', 'temporary-chat', 'server-chat');

    expect(readComposerDraft('user-a', 'server-chat')).toBe('下一条问题\n ');
    expect(readComposerDraftRevision('user-a', 'server-chat')).toBe(sourceRevision);
    expect(readComposerDraft('user-a', 'temporary-chat')).toBe('');
    expect(readComposerDraft('user-b', 'temporary-chat')).toBe('其他账号草稿');
  });

  it('迁移时目标已有非空草稿则保留目标并删除源', () => {
    writeComposerDraft('user-a', null, '新对话草稿');
    writeComposerDraft('user-a', 'chat-a', '目标草稿');
    const targetRevision = readComposerDraftRevision('user-a', 'chat-a');

    moveComposerDraft('user-a', null, 'chat-a');

    expect(readComposerDraft('user-a', 'chat-a')).toBe('目标草稿');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBe(targetRevision);
    expect(readComposerDraft('user-a', null)).toBe('');
  });

  it('迁移到当前作用域时不删除草稿', () => {
    writeComposerDraft('user-a', 'chat-a', '继续编辑');

    moveComposerDraft('user-a', 'chat-a', 'chat-a');

    expect(readComposerDraft('user-a', 'chat-a')).toBe('继续编辑');
  });

  it('源草稿不存在时保留目标且不创建空草稿', () => {
    writeComposerDraft('user-a', 'chat-a', '目标草稿');

    moveComposerDraft('user-a', null, 'chat-a');
    moveComposerDraft('user-a', null, 'chat-b');

    expect(readComposerDraft('user-a', 'chat-a')).toBe('目标草稿');
    expect(readComposerDraft('user-a', 'chat-b')).toBe('');
    expect(sessionStorage.length).toBe(1);
  });

  it('迁移写入受限时保留源草稿', () => {
    writeComposerDraft('user-a', null, '尚未迁移');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });

    expect(() => moveComposerDraft('user-a', null, 'chat-a')).not.toThrow();
    expect(readComposerDraft('user-a', null)).toBe('尚未迁移');
    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
  });

  it('匿名输入不读取或写入持久化存储', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem');

    writeComposerDraft(null, 'chat-a', '匿名输入');
    writeComposerDraft('', null, '匿名新对话');
    expect(readComposerDraft(null, 'chat-a')).toBe('');
    expect(readComposerDraftRevision(null, 'chat-a')).toBeNull();
    clearComposerDraftIfUnchanged(null, 'chat-a', '匿名输入');
    moveComposerDraft(null, null, 'chat-a');

    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).not.toHaveBeenCalled();
    expect(removeItem).not.toHaveBeenCalled();
  });

  it.each(['{broken json', '{"text":"invalid shape"}', '{"text":"invalid revision","revision":42}', '42', 'null'])('忽略损坏或不符合文本类型的存储值：%s', (storedValue) => {
    writeComposerDraft('user-a', 'chat-a', '原草稿');
    const key = sessionStorage.key(0)!;
    sessionStorage.setItem(key, storedValue);

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();
    expect(() => clearComposerDraftIfUnchanged('user-a', 'chat-a', '原草稿')).not.toThrow();
  });

  it('读取受限时返回空草稿，条件清除也不抛出异常', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();
    expect(() => clearComposerDraftIfUnchanged('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => moveComposerDraft('user-a', null, 'chat-a')).not.toThrow();
  });

  it('写入或删除受限时不把异常抛给输入流程', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });

    expect(() => writeComposerDraft('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => writeComposerDraft('user-a', 'chat-a', '')).not.toThrow();
  });

  it('sessionStorage 属性访问受限时静默回退', () => {
    vi.spyOn(window, 'sessionStorage', 'get').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();
    expect(() => writeComposerDraft('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => clearComposerDraftIfUnchanged('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => moveComposerDraft('user-a', null, 'chat-a')).not.toThrow();
  });

  it('SSR 没有 window 时安全返回且不写入', () => {
    vi.stubGlobal('window', undefined);

    expect(readComposerDraft('user-a', 'chat-a')).toBe('');
    expect(readComposerDraftRevision('user-a', 'chat-a')).toBeNull();
    expect(() => writeComposerDraft('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => clearComposerDraftIfUnchanged('user-a', 'chat-a', '问题')).not.toThrow();
    expect(() => moveComposerDraft('user-a', null, 'chat-a')).not.toThrow();
  });
});
