// 旧版把聊天与设置镜像到 IndexedDB「ai-assistant-db」，现已不再读写；启动时删除残留库。
const LEGACY_DATABASE_NAME = 'ai-assistant-db';

export function dropLegacyLocalDatabase(): void {
  try {
    if (typeof indexedDB === 'undefined') return;
    // 库不存在时删除也会直接成功；旧标签页仍持有连接时会排队，关闭后由浏览器完成。
    indexedDB.deleteDatabase(LEGACY_DATABASE_NAME);
  } catch {
    // 隐私模式等环境可能禁用 IndexedDB，残留库不影响功能。
  }
}
