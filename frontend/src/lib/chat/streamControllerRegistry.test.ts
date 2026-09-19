import { beforeEach, describe, expect, it } from 'vitest';

import {
  abortStreamController,
  getStreamController,
  migrateStreamController,
  registerStreamController,
  releaseStreamController,
  resetStreamControllerRegistry,
  updateStreamController,
} from './streamControllerRegistry';

function entry(conversationId: string, kind: 'send' | 'recovery' | 'continuation' = 'send') {
  return { conversationId, kind, controller: new AbortController() };
}

describe('streamControllerRegistry', () => {
  beforeEach(() => {
    resetStreamControllerRegistry();
  });

  it('登记后能按会话取回', () => {
    const a = entry('conv-a');
    registerStreamController(a);

    expect(getStreamController('conv-a')).toBe(a);
    expect(getStreamController('conv-b')).toBeNull();
    expect(getStreamController(null)).toBeNull();
  });

  it('两个会话互不影响（本次重构的核心要求）', () => {
    // 此前三个控制器 ref 各自为政，停止只能按「哪个 ref 非空」猜；
    // 并发时在 A 点停止会误停 B。
    const a = entry('conv-a', 'send');
    const b = entry('conv-b', 'recovery');
    registerStreamController(a);
    registerStreamController(b);

    const aborted = abortStreamController('conv-a');

    expect(aborted).toBe(a);
    expect(a.controller.signal.aborted).toBe(true);
    expect(b.controller.signal.aborted).toBe(false);
    expect(getStreamController('conv-b')).toBe(b);
  });

  it('迟到的注销不得注销后来者', () => {
    // 与 streamSlice 的 ownsStreamSlot 同源的教训：旧流的收尾回调可能在新流
    // 已经登记之后才到。这次由结构保证，而不是在每个调用点补判据。
    const stale = entry('conv-a');
    registerStreamController(stale);
    const fresh = entry('conv-a');
    registerStreamController(fresh);

    expect(releaseStreamController('conv-a', stale.controller)).toBe(false);
    expect(getStreamController('conv-a')).toBe(fresh);

    expect(releaseStreamController('conv-a', fresh.controller)).toBe(true);
    expect(getStreamController('conv-a')).toBeNull();
  });

  it('覆盖登记不自动中止旧流，由调用方决定', () => {
    // 注册表只记录「现在谁在跑」；是否中止旧流是业务语义。
    const first = entry('conv-a');
    registerStreamController(first);
    const second = entry('conv-a');
    registerStreamController(second);

    expect(first.controller.signal.aborted).toBe(false);
    expect(getStreamController('conv-a')).toBe(second);
  });

  it('草稿转正时迁移键，旧键不留残条', () => {
    // 发送流起于草稿 ID，首个响应带回真实会话 ID 后 activeConvIdRef 会改写
    // （useSendMessage 的 materializeIfNeeded）。键不跟着迁移，这条流就会在新 ID 下
    // 查不到、在旧 ID 下变成永不注销的泄漏。
    const draft = entry('temp-conv');
    registerStreamController(draft);

    expect(migrateStreamController('temp-conv', 'server-conv', draft.controller)).toBe(true);
    expect(getStreamController('temp-conv')).toBeNull();
    expect(getStreamController('server-conv')).toMatchObject({
      conversationId: 'server-conv',
      controller: draft.controller,
    });
    // 迁移后按新键注销仍要认得同一个 controller
    expect(releaseStreamController('server-conv', draft.controller)).toBe(true);
  });

  it('控制器不符或同名时不迁移', () => {
    const a = entry('temp-conv');
    registerStreamController(a);

    expect(migrateStreamController('temp-conv', 'temp-conv', a.controller)).toBe(false);
    expect(migrateStreamController('temp-conv', 'server-conv', new AbortController())).toBe(false);
    expect(getStreamController('temp-conv')).toBe(a);
    expect(getStreamController('server-conv')).toBeNull();
  });

  it('中止不存在的会话返回 null，不抛错', () => {
    expect(abortStreamController('conv-missing')).toBeNull();
    expect(abortStreamController(null)).toBeNull();
    expect(releaseStreamController(null, new AbortController())).toBe(false);
  });

  it('补充元数据只对当前条目生效', () => {
    const a = entry('conv-a', 'recovery');
    registerStreamController(a);
    updateStreamController('conv-a', a.controller, { streamMode: 'retry', taskId: 'task-1' });

    expect(getStreamController('conv-a')).toMatchObject({ streamMode: 'retry', taskId: 'task-1' });

    // 条目已被换掉后，旧控制器的迟到补充不得写入
    const next = entry('conv-a', 'send');
    registerStreamController(next);
    updateStreamController('conv-a', a.controller, { taskId: 'stale-task' });

    expect(getStreamController('conv-a')?.taskId).toBeUndefined();
  });
});
