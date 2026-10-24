import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  fetchAdminShowcaseImagesMock,
  uploadShowcaseImageMock,
  deleteShowcaseImageMock,
  fetchGeneratedShowcaseCandidatesMock,
  addShowcaseFromGeneratedMock,
} = vi.hoisted(() => ({
  fetchAdminShowcaseImagesMock: vi.fn(),
  uploadShowcaseImageMock: vi.fn(),
  deleteShowcaseImageMock: vi.fn(),
  fetchGeneratedShowcaseCandidatesMock: vi.fn(),
  addShowcaseFromGeneratedMock: vi.fn(),
}));

vi.mock('@/lib/api/prompts', () => ({
  fetchAdminShowcaseImages: fetchAdminShowcaseImagesMock,
  uploadShowcaseImage: uploadShowcaseImageMock,
  deleteShowcaseImage: deleteShowcaseImageMock,
  fetchGeneratedShowcaseCandidates: fetchGeneratedShowcaseCandidatesMock,
  addShowcaseFromGenerated: addShowcaseFromGeneratedMock,
}));

import HomeShowcaseManager from './HomeShowcaseManager';

function candidate(fileId: string, prompt: string, extra: { title?: string | null; inPool?: boolean } = {}) {
  return {
    file_id: fileId,
    prompt,
    title: extra.title ?? null,
    width: 864,
    height: 864,
    thumbnail_url: `/signed/${fileId}`,
    in_pool: extra.inPool ?? false,
  };
}

function showcaseImage(id: string, label: string, prompt: string, sourceFileId: string | null = null) {
  return {
    id,
    label,
    prompt,
    object_position: null,
    source_file_id: sourceFileId,
    image_url: `/api/prompts/showcase/${id}/image`,
    width: 480,
    height: 600,
  };
}

describe('HomeShowcaseManager', () => {
  beforeEach(() => {
    fetchAdminShowcaseImagesMock.mockReset();
    uploadShowcaseImageMock.mockReset();
    deleteShowcaseImageMock.mockReset();
    fetchGeneratedShowcaseCandidatesMock.mockReset();
    addShowcaseFromGeneratedMock.mockReset();
    fetchGeneratedShowcaseCandidatesMock.mockResolvedValue({
      groups: [
        { key: 'f-new', versions: [candidate('f-new', '一张通货膨胀成因的信息图海报', { title: '信息图' })] },
        { key: 'f-old', versions: [candidate('f-old', '水墨白鹭', { inPool: true })] },
      ],
      next_offset: null,
    });
    fetchAdminShowcaseImagesMock.mockResolvedValue({
      images: [showcaseImage('cat', '水彩插画', '画一只水彩风格的橘猫')],
    });
  });

  it('从自己的生成图挑选，标签和提示词默认沿用生成时的名称和原文，加入后标为已在图片池', async () => {
    addShowcaseFromGeneratedMock.mockResolvedValue(
      showcaseImage('infographic', '信息图', '一张通货膨胀成因的信息图海报', 'f-new'),
    );
    render(<HomeShowcaseManager />);
    fireEvent.click(await screen.findByLabelText('隐藏已在图片池的图'));

    const picker = await screen.findByRole('list', { name: '我的生成图' });
    expect(fetchGeneratedShowcaseCandidatesMock).toHaveBeenLastCalledWith({ hideInPool: false });
    expect(within(picker).getAllByText('已在图片池')).toHaveLength(1);
    fireEvent.click(within(picker).getByRole('button', { name: '一张通货膨胀成因的信息图海报' }));
    expect(screen.getByLabelText('做同款提示词')).toHaveValue('一张通货膨胀成因的信息图海报');
    expect(screen.getByLabelText('标签')).toHaveValue('信息图');

    fireEvent.click(screen.getByRole('button', { name: /添加到图片池/ }));

    await waitFor(() => expect(addShowcaseFromGeneratedMock).toHaveBeenCalledWith({
      fileId: 'f-new',
      label: '信息图',
      prompt: '一张通货膨胀成因的信息图海报',
      objectPosition: null,
    }));
    await waitFor(() => expect(within(picker).getAllByText('已在图片池')).toHaveLength(2));
    expect(uploadShowcaseImageMock).not.toHaveBeenCalled();

    // 从池里删掉后，挑选列表里的标记跟着撤掉
    deleteShowcaseImageMock.mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole('button', { name: '删除信息图' }));
    fireEvent.click(screen.getByRole('button', { name: '确认删除信息图' }));
    await waitFor(() => expect(within(picker).getAllByText('已在图片池')).toHaveLength(1));

    // 旧图没有名称，标签留空等手填
    fireEvent.click(within(picker).getByRole('button', { name: '水墨白鹭' }));
    expect(screen.getByLabelText('标签')).toHaveValue('');
  });

  it('默认隐藏已在图片池的图，刚加进池的图也立刻收起', async () => {
    addShowcaseFromGeneratedMock.mockResolvedValue(
      showcaseImage('infographic', '信息图', '一张通货膨胀成因的信息图海报', 'f-new'),
    );
    fetchGeneratedShowcaseCandidatesMock.mockResolvedValue({
      groups: [
        { key: 'f-new', versions: [candidate('f-new', '一张通货膨胀成因的信息图海报', { title: '信息图' })] },
        { key: 'f-b', versions: [candidate('f-b', '另一张')] },
      ],
      next_offset: null,
    });
    render(<HomeShowcaseManager />);

    const picker = await screen.findByRole('list', { name: '我的生成图' });
    expect(fetchGeneratedShowcaseCandidatesMock).toHaveBeenCalledWith({ hideInPool: true });
    fireEvent.click(within(picker).getByRole('button', { name: '一张通货膨胀成因的信息图海报' }));
    fireEvent.click(screen.getByRole('button', { name: /添加到图片池/ }));

    await waitFor(() => expect(
      within(picker).queryByRole('button', { name: '一张通货膨胀成因的信息图海报' }),
    ).toBeNull());
    expect(within(picker).getByRole('button', { name: '另一张' })).toBeInTheDocument();
  });

  it('切换筛选后先发的请求晚到，不覆盖新列表', async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    fetchGeneratedShowcaseCandidatesMock
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ groups: [{ key: 'f-all', versions: [candidate('f-all', '全部列表')] }], next_offset: null });
    render(<HomeShowcaseManager />);

    fireEvent.click(screen.getByLabelText('隐藏已在图片池的图'));
    const picker = await screen.findByRole('list', { name: '我的生成图' });
    resolveFirst({ groups: [{ key: 'f-hidden', versions: [candidate('f-hidden', '旧请求')] }], next_offset: null });

    await waitFor(() => expect(fetchGeneratedShowcaseCandidatesMock).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(picker).getByRole('button', { name: '全部列表' })).toBeInTheDocument();
    expect(within(picker).queryByRole('button', { name: '旧请求' })).toBeNull();
  });

  it('多次编辑的版本合成一组显示最新的一张，展开后可以挑任意版本', async () => {
    fetchGeneratedShowcaseCandidatesMock.mockResolvedValue({
      groups: [{
        key: 'f-1',
        versions: [candidate('f-3', '第三版'), candidate('f-2', '第二版', { title: '家常菜' }), candidate('f-1', '初版')],
      }],
      next_offset: null,
    });
    render(<HomeShowcaseManager />);

    const picker = await screen.findByRole('list', { name: '我的生成图' });
    expect(within(picker).getByRole('button', { name: '第三版' })).toBeInTheDocument();
    expect(within(picker).queryByRole('button', { name: '第二版' })).toBeNull();

    fireEvent.click(within(picker).getByRole('button', { name: '3 个版本' }));
    const versions = screen.getByRole('list', { name: '同一张图的各个版本' });
    fireEvent.click(within(versions).getByRole('button', { name: '第二版' }));

    expect(screen.getByLabelText('做同款提示词')).toHaveValue('第二版');
    expect(screen.getByLabelText('标签')).toHaveValue('家常菜');
    expect(within(versions).getByRole('button', { name: '第二版' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('按组分页加载更早的图，跨页重复的组只保留一份', async () => {
    fetchGeneratedShowcaseCandidatesMock
      .mockResolvedValueOnce({
        groups: [{ key: 'f-1', versions: [candidate('f-1', '第一页')] }],
        next_offset: 20,
      })
      .mockResolvedValueOnce({
        groups: [
          { key: 'f-1', versions: [candidate('f-1', '第一页')] },
          { key: 'f-2', versions: [candidate('f-2', '第二页')] },
        ],
        next_offset: null,
      });
    render(<HomeShowcaseManager />);

    const picker = await screen.findByRole('list', { name: '我的生成图' });
    fireEvent.click(within(picker).getByRole('button', { name: '加载更早的图' }));

    await waitFor(() => expect(within(picker).getByRole('button', { name: '第二页' })).toBeInTheDocument());
    expect(fetchGeneratedShowcaseCandidatesMock).toHaveBeenLastCalledWith({ offset: 20, hideInPool: true });
    expect(within(picker).getAllByRole('button', { name: '第一页' })).toHaveLength(1);
    expect(within(picker).queryByRole('button', { name: '加载更早的图' })).toBeNull();
  });

  it('上传图片、标签、提示词和裁切位置后追加到图片池末尾', async () => {
    uploadShowcaseImageMock.mockResolvedValue(showcaseImage('poster', '中文海报', '设计一张咖啡店开业海报'));
    render(<HomeShowcaseManager />);
    expect(await screen.findByText('水彩插画')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('上传图片'));

    const submit = screen.getByRole('button', { name: /添加到图片池/ });
    expect(submit).toBeDisabled();

    const file = new File(['png'], 'poster.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('图片'), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText('标签'), { target: { value: ' 中文海报 ' } });
    fireEvent.change(screen.getByLabelText('做同款提示词'), { target: { value: '设计一张咖啡店开业海报' } });
    fireEvent.click(screen.getByLabelText('保留顶部'));
    fireEvent.click(submit);

    await waitFor(() => expect(uploadShowcaseImageMock).toHaveBeenCalledWith({
      file,
      label: '中文海报',
      prompt: '设计一张咖啡店开业海报',
      objectPosition: 'top',
    }));
    const pool = screen.getByRole('list', { name: '图片池' });
    const items = await within(pool).findAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('水彩插画'),
      expect.stringContaining('中文海报'),
    ]);
    expect(screen.getByLabelText('标签')).toHaveValue('');
  });

  it('上传失败时展示后端给出的原因', async () => {
    uploadShowcaseImageMock.mockRejectedValue(new Error('图片无法读取，请换一张'));
    render(<HomeShowcaseManager />);
    await screen.findByText('水彩插画');
    fireEvent.click(screen.getByLabelText('上传图片'));

    fireEvent.change(screen.getByLabelText('图片'), { target: { files: [new File(['x'], 'x.png', { type: 'image/png' })] } });
    fireEvent.change(screen.getByLabelText('标签'), { target: { value: '海报' } });
    fireEvent.change(screen.getByLabelText('做同款提示词'), { target: { value: '海报' } });
    fireEvent.click(screen.getByRole('button', { name: /添加到图片池/ }));

    expect(await screen.findByText('图片无法读取，请换一张')).toBeInTheDocument();
  });

  it('删除要再点一次确认', async () => {
    deleteShowcaseImageMock.mockResolvedValue(undefined);
    render(<HomeShowcaseManager />);

    fireEvent.click(await screen.findByRole('button', { name: '删除水彩插画' }));
    expect(deleteShowcaseImageMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除水彩插画' }));
    await waitFor(() => expect(deleteShowcaseImageMock).toHaveBeenCalledWith('cat'));
    expect(await screen.findByText('图片池是空的，首页不会展示生图示例')).toBeInTheDocument();
  });
});
