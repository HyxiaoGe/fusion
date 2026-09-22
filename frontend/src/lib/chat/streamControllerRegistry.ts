/** 按会话索引的流控制器注册表。
 *
 * 此前发送流、恢复流、续跑各自持有一个 AbortController ref，分别活在
 * useSendMessage、chat/[chatId]/page.tsx、useContinueAgentRun 里，互不知情。
 * 停止时只能按「哪个 ref 非空」依次猜：
 *
 *     if (reconnectControllerRef.current) { …停恢复流… }
 *     else if (await stopContinueAgentRun()) { … }
 *     else await stopStreaming();
 *
 * 单会话下同时只有一条流，猜得中；多会话并发后必然猜错——在 A 点停止，若 B 恰好
 * 有恢复流使 reconnectControllerRef 非空，停掉的会是 B。本模块把「猜」换成「查」。
 *
 * 只登记控制器身份与定位所需的元数据，不承载流的业务状态（正文、timeline 等仍在
 * streamSlice）。模块级单例：控制器本身就是进程级的活对象，且与 useSendMessage 中
 * 既有的 activeSendPreparations 保持同一形态。
 */

export type StreamKind = 'send' | 'recovery' | 'continuation';

/** 恢复流从 stream-status 得到的模式，决定停止时如何收口。 */
export type RecoveryStreamMode = 'initial' | 'retry' | 'continuation';

export interface StreamControllerEntry {
  conversationId: string;
  kind: StreamKind;
  controller: AbortController;
  /** 仅恢复流有值。 */
  streamMode?: RecoveryStreamMode | null;
  /** 服务端任务 ID。发送流在 onReady 写入；恢复流在 stream-status / onReady 写入。 */
  taskId?: string | null;
  /** 服务端 assistant message ID。不要写入本地 placeholder。 */
  messageId?: string | null;
}

const registry = new Map<string, StreamControllerEntry>();

/** 登记一条流。同一会话同时只会有一条，重复登记按新的覆盖。
 *
 * 不在这里 abort 被覆盖的旧条目：是否中止旧流由调用方按业务语义决定，
 * 注册表只记录「现在谁在跑」。
 */
export function registerStreamController(entry: StreamControllerEntry): void {
  registry.set(entry.conversationId, entry);
}

export function getStreamController(conversationId: string | null | undefined): StreamControllerEntry | null {
  if (!conversationId) return null;
  return registry.get(conversationId) ?? null;
}

/** 补充 stream-status / onReady 之后才知道的停止身份。条目已被换掉时不写入。 */
export function updateStreamController(
  conversationId: string,
  controller: AbortController,
  patch: Partial<Pick<StreamControllerEntry, 'streamMode' | 'taskId' | 'messageId'>>,
): void {
  const current = registry.get(conversationId);
  if (!current || current.controller !== controller) return;
  registry.set(conversationId, { ...current, ...patch });
}

/** 注销。**只在 controller 相符时生效**——迟到的收尾不得注销后来者。
 *
 * 这是本模块存在的核心不变量：与 streamSlice 的 ownsStreamSlot 同源的教训，
 * 只是这次由结构保证，而不是在每个调用点补判据。
 *
 * @returns 是否真的注销了
 */
export function releaseStreamController(
  conversationId: string | null | undefined,
  controller: AbortController,
): boolean {
  if (!conversationId) return false;
  const current = registry.get(conversationId);
  if (!current || current.controller !== controller) return false;
  registry.delete(conversationId);
  return true;
}

/** 草稿会话转正时把条目迁到服务端会话 ID 下。
 *
 * 发送流起于草稿 ID（tempConvId），首个响应带回真实会话 ID 后 activeConvIdRef 会改写，
 * 注册表的键必须同步迁移，否则这条流在新 ID 下查不到、在旧 ID 下变成永不注销的泄漏。
 * 与 streamSlice 的 migrateStreamConversation 对应。
 *
 * 控制器不符（条目已被换掉）时不迁移。
 */
export function migrateStreamController(
  fromConversationId: string,
  toConversationId: string,
  controller: AbortController,
): boolean {
  if (fromConversationId === toConversationId) return false;
  const current = registry.get(fromConversationId);
  if (!current || current.controller !== controller) return false;
  registry.delete(fromConversationId);
  registry.set(toConversationId, { ...current, conversationId: toConversationId });
  return true;
}

/** 中止指定会话正在跑的流并注销；没有则返回 null。
 *
 * @returns 被中止的条目，供调用方按 kind 决定后续收口动作
 */
export function abortStreamController(
  conversationId: string | null | undefined,
): StreamControllerEntry | null {
  if (!conversationId) return null;
  const current = registry.get(conversationId);
  if (!current) return null;
  registry.delete(conversationId);
  current.controller.abort();
  return current;
}

/** 仅测试与登出等全局失效场景使用。 */
export function resetStreamControllerRegistry(): void {
  registry.clear();
}
