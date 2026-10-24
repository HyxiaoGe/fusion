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
      images: [
        { file_id: 'f-new', prompt: '一张通货膨胀成因的信息图海报', width: 864, height: 1312, thumbnail_url: '/signed/f-new', in_pool: false },
        { file_id: 'f-old', prompt: '水墨白鹭', width: 864, height: 864, thumbnail_url: '/signed/f-old', in_pool: true },
      ],
    });
    fetchAdminShowcaseImagesMock.mockResolvedValue({
      images: [showcaseImage('cat', '水彩插画', '画一只水彩风格的橘猫')],
    });
  });

  it('从自己的生成图挑选，提示词默认沿用生成时的原文，加入后标为已在图片池', async () => {
    addShowcaseFromGeneratedMock.mockResolvedValue(
      showcaseImage('infographic', '信息图', '一张通货膨胀成因的信息图海报', 'f-new'),
    );
    render(<HomeShowcaseManager />);

    const picker = await screen.findByRole('list', { name: '我的生成图' });
    expect(within(picker).getAllByText('已在图片池')).toHaveLength(1);
    fireEvent.click(within(picker).getByRole('button', { name: '一张通货膨胀成因的信息图海报' }));
    expect(screen.getByLabelText('做同款提示词')).toHaveValue('一张通货膨胀成因的信息图海报');

    fireEvent.change(screen.getByLabelText('标签'), { target: { value: '信息图' } });
    fireEvent.click(screen.getByRole('button', { name: /添加到图片池/ }));

    await waitFor(() => expect(addShowcaseFromGeneratedMock).toHaveBeenCalledWith({
      fileId: 'f-new',
      label: '信息图',
      prompt: '一张通货膨胀成因的信息图海报',
      objectPosition: null,
    }));
    await waitFor(() => expect(within(picker).getAllByText('已在图片池')).toHaveLength(2));
    expect(uploadShowcaseImageMock).not.toHaveBeenCalled();
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
