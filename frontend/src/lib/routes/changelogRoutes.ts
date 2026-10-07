export const CHANGELOG_PATH = '/updates';

export function buildChangelogPath(id: string): string {
  return `${CHANGELOG_PATH}/${encodeURIComponent(id)}`;
}
