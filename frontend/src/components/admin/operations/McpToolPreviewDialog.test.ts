import { describe, expect, it } from 'vitest';
import { formatResetIn } from './McpToolPreviewDialog';

describe('额度恢复时间', () => {
  it('按月配额可能跨几十天，超过一天按天显示', () => {
    expect(formatResetIn(22 * 86400 + 60)).toBe('约 22 天后恢复');
    expect(formatResetIn(7200)).toBe('约 2 小时后恢复');
    expect(formatResetIn(30)).toBe('约 1 分钟后恢复');
  });
});
