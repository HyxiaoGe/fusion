import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearStopOutcomeNotice,
  readStopOutcomeNotice,
  saveStopOutcomeNotice,
} from './stopOutcomeNotice';

const notice = {
  authIdentity: 'user-a',
  conversationId: 'chat-a',
  runId: 'run-a',
  messageId: 'assistant-a',
  requestedAt: 1_000_000,
  terminalStatus: null,
} as const;

describe('停止结果刷新提示', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it('只在同一账号和会话读取，旧运行不能清除新运行的提示', () => {
    vi.useFakeTimers();
    vi.setSystemTime(notice.requestedAt);
    saveStopOutcomeNotice(notice);
    expect(readStopOutcomeNotice('chat-a', 'user-a')).toEqual(notice);
    expect(readStopOutcomeNotice('chat-b', 'user-a')).toBeNull();

    saveStopOutcomeNotice({ ...notice, runId: 'run-b' });
    clearStopOutcomeNotice('chat-a', 'run-a');
    expect(readStopOutcomeNotice('chat-a', 'user-a')?.runId).toBe('run-b');
    expect(readStopOutcomeNotice('chat-a', 'user-b')).toBeNull();
    vi.useRealTimers();
  });

  it('超过一天或内容无效时不恢复旧提示', () => {
    vi.useFakeTimers();
    vi.setSystemTime(notice.requestedAt + 24 * 60 * 60 * 1000 + 1);
    saveStopOutcomeNotice(notice);
    expect(readStopOutcomeNotice('chat-a', 'user-a')).toBeNull();
    sessionStorage.setItem('fusion:stop-outcome:chat-a', '{bad json');
    expect(readStopOutcomeNotice('chat-a', 'user-a')).toBeNull();
    vi.useRealTimers();
  });
});
