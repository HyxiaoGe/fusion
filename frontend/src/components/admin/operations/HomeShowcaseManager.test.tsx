import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { fetchAdminShowcaseImagesMock, uploadShowcaseImageMock, deleteShowcaseImageMock } = vi.hoisted(() => ({
  fetchAdminShowcaseImagesMock: vi.fn(),
  uploadShowcaseImageMock: vi.fn(),
  deleteShowcaseImageMock: vi.fn(),
}));

vi.mock('@/lib/api/prompts', () => ({
  fetchAdminShowcaseImages: fetchAdminShowcaseImagesMock,
  uploadShowcaseImage: uploadShowcaseImageMock,
  deleteShowcaseImage: deleteShowcaseImageMock,
}));

import HomeShowcaseManager from './HomeShowcaseManager';

function showcaseImage(id: string, label: string, prompt: string) {
  return {
    id,
    label,
    prompt,
    object_position: null,
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
    fetchAdminShowcaseImagesMock.mockResolvedValue({
      images: [showcaseImage('cat', '水彩插画', '画一只水彩风格的橘猫')],
    });
  });

  it('上传图片、标签、提示词和裁切位置后追加到图片池末尾', async () => {
    uploadShowcaseImageMock.mockResolvedValue(showcaseImage('poster', '中文海报', '设计一张咖啡店开业海报'));
    render(<HomeShowcaseManager />);
    expect(await screen.findByText('水彩插画')).toBeInTheDocument();

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
    const items = await screen.findAllByRole('listitem');
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
