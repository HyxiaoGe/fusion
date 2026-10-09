// @vitest-environment node

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = process.cwd();

function source(path: string): string {
  return readFileSync(resolve(root, path), 'utf8');
}

describe('Trajectory P3 旧过程迁移闸门', () => {
  it('聊天产品装配不再挂载或引用旧过程组件和模型', () => {
    const productComposition = [
      'src/app/(app)/chat/[chatId]/page.tsx',
      'src/components/chat/ChatMessageList.tsx',
      'src/components/chat/ChatMessage.tsx',
      'src/components/chat/AssistantMessage.tsx',
      'src/components/chat/AssistantResponseStack.tsx',
    ];

    for (const path of productComposition) {
      expect(source(path), path).not.toMatch(
        /AgentRunTimeline|ExecutionProcess|buildExecutionProcessModel|executionProcessModel/,
      );
    }
  });

  it('Agent run continue 不再穿过聊天消息链路，响应栈也不接收旧过程参数', () => {
    const messageChain = [
      'src/components/chat/ChatMessageList.tsx',
      'src/components/chat/ChatMessage.tsx',
      'src/components/chat/AssistantMessage.tsx',
    ];

    for (const path of messageChain) {
      expect(source(path), path).not.toContain('onContinueAgentRun');
    }

    const responseStack = source('src/components/chat/AssistantResponseStack.tsx');
    expect(responseStack).not.toMatch(/onRetry|onContinueAgentRun|searchQueries/);
  });
});
