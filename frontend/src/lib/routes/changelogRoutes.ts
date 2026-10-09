export const CHANGELOG_PATH = '/updates';
export const CHANGELOG_ENTRY_PARAM = 'entry';

/** 更新日志在同一页按时间线展开，单篇入口是带定位参数的列表页。 */
export function buildChangelogPath(id: string): string {
  return `${CHANGELOG_PATH}?${CHANGELOG_ENTRY_PARAM}=${encodeURIComponent(id)}`;
}

export function changelogAnchorId(id: string): string {
  return `changelog-${id}`;
}
