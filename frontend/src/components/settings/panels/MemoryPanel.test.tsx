import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zh from '@/lib/i18n/locales/zh-CN.json';

const api = vi.hoisted(() => ({
  getMemories: vi.fn(),
  setMemoryEnabled: vi.fn(),
  deleteMemory: vi.fn(),
  clearMemories: vi.fn(),
}));

vi.mock('@/lib/api/memories', () => api);

function translate(key: string, values?: Record<string, unknown>) {
  const value = key.split('.').reduce<unknown>((current, part) => (
    current && typeof current === 'object' ? (current as Record<string, unknown>)[part] : undefined
  ), zh);
  if (typeof value !== 'string') throw new Error(`缺少测试所需翻译: ${key}`);
  return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ''));
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: translate, i18n: { language: 'zh-CN' } }),
}));

import MemoryPanel from './MemoryPanel';

const item = (id: string, category: string, content: string) => ({
  id, category, content, source_conversation_id: null, updated_at: '2026-10-14T08:00:00Z',
});

describe('MemoryPanel 记忆设置', () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
  });

  it('展示记忆规则，并按分类分组显示条目', async () => {
    api.getMemories.mockResolvedValue({
      enabled: true,
      items: [item('i1', 'interests', '喜欢徒步'), item('h1', 'health', '2 型糖尿病'), item('x1', 'pets', '养了一只猫')],
    });
    render(<MemoryPanel />);

    expect(screen.getByText(translate('settings.memory.rules.skip'))).toBeInTheDocument();
    expect(screen.getByText(translate('settings.memory.rules.when'))).toBeInTheDocument();
    await screen.findByText('2 型糖尿病');

    const headings = screen.getAllByRole('heading', { level: 4 }).map((node) => node.textContent);
    expect(headings).toEqual(['健康', '兴趣', 'pets']);
    expect(screen.getByRole('switch', { name: '启用记忆' })).toBeChecked();
  });

  it('删除单条后从列表移除，失败时提示', async () => {
    api.getMemories.mockResolvedValue({ enabled: true, items: [item('r1', 'residence', '杭州西湖区')] });
    api.deleteMemory.mockRejectedValueOnce(new Error('x')).mockResolvedValueOnce({ deleted: true });
    render(<MemoryPanel />);
    const remove = await screen.findByRole('button', { name: '删除这条记忆：杭州西湖区' });

    fireEvent.click(remove);
    expect(await screen.findByRole('alert')).toHaveTextContent('删除失败');

    fireEvent.click(remove);
    await waitFor(() => expect(screen.queryByText('杭州西湖区')).toBeNull());
    expect(api.deleteMemory).toHaveBeenLastCalledWith('r1');
    expect(screen.getByText(translate('settings.memory.empty'))).toBeInTheDocument();
  });

  it('清空需确认，开关调用设置接口', async () => {
    api.getMemories.mockResolvedValue({ enabled: true, items: [item('n1', 'name', '小王')] });
    api.clearMemories.mockResolvedValue({ deleted: 1 });
    api.setMemoryEnabled.mockResolvedValue({ enabled: false });
    render(<MemoryPanel />);
    await screen.findByText('小王');

    fireEvent.click(screen.getByRole('button', { name: '全部清空' }));
    expect(api.clearMemories).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '清空' }));
    await waitFor(() => expect(screen.queryByText('小王')).toBeNull());

    fireEvent.click(screen.getByRole('switch', { name: '启用记忆' }));
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用记忆' })).not.toBeChecked());
    expect(api.setMemoryEnabled).toHaveBeenCalledWith(false);
  });

  it('加载失败可重试', async () => {
    api.getMemories.mockRejectedValueOnce(new Error('x')).mockResolvedValueOnce({ enabled: false, items: [] });
    render(<MemoryPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '重试' }));
    await screen.findByText(translate('settings.memory.empty'));
    expect(screen.getByRole('switch', { name: '启用记忆' })).not.toBeChecked();
  });
});
