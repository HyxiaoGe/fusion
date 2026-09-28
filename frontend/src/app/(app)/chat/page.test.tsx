import { beforeEach, describe, expect, it, vi } from 'vitest';

const { redirectMock } = vi.hoisted(() => ({
  redirectMock: vi.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  }),
}));

vi.mock('next/navigation', () => ({
  redirect: redirectMock,
}));

import ChatIndex from './page';

describe('/chat 路由规范化', () => {
  beforeEach(() => {
    redirectMock.mockClear();
  });

  it('/chat 重定向到 /chat/new', async () => {
    await expect(ChatIndex({ searchParams: Promise.resolve({}) })).rejects.toThrow('NEXT_REDIRECT:/chat/new');
  });

  it('/chat?model=deepseek-chat 保留模型参数', async () => {
    await expect(
      ChatIndex({ searchParams: Promise.resolve({ model: 'deepseek-chat' }) }),
    ).rejects.toThrow('NEXT_REDIRECT:/chat/new?model=deepseek-chat');
  });
});
