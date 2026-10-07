import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChangelogContent } from './ChangelogContent';

describe('更新日志正文', () => {
  afterEach(cleanup);

  it('渲染 Markdown 并阻断原始 HTML、危险链接和远程图片请求', () => {
    const { container } = render(<ChangelogContent content={'## 新功能\n\n- **通知中心**\n\n<script>alert(1)</script>\n\n[危险](javascript:alert%281%29)\n\n[文档](https://example.com/docs)\n\n![说明图片](https://example.com/tracking.png)'} />);
    expect(screen.getByRole('heading', { name: '新功能' })).toBeVisible();
    expect(screen.getByText('通知中心').tagName).toBe('STRONG');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('危险').tagName).toBe('SPAN');
    expect(screen.getByRole('link', { name: '文档' })).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
