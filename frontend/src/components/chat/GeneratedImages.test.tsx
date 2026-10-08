import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import GeneratedImages from './GeneratedImages';
import { getFileUrl } from '@/lib/api/files';
import type { GeneratedImageBlock } from '@/types/conversation';

vi.mock('@/lib/api/files', () => ({
  getFileUrl: vi.fn(),
}));

const getFileUrlMock = vi.mocked(getFileUrl);

const block: GeneratedImageBlock = {
  type: 'generated_image',
  id: 'blk-img',
  schema_version: 1,
  provider: 'image-service',
  file_id: 'file-1',
  mime_type: 'image/jpeg',
  width: 1024,
  height: 1024,
  prompt: '一只红狐狸',
  aspect_ratio: '1:1',
  model: null,
};

describe('GeneratedImages', () => {
  beforeEach(() => {
    getFileUrlMock.mockReset();
  });

  it('renders nothing without blocks', () => {
    const { container } = render(<GeneratedImages blocks={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('loads the processed image by file id and opens the viewer on click', async () => {
    getFileUrlMock.mockResolvedValue('/api/files/file-1/content?variant=processed&token=t');

    render(<GeneratedImages blocks={[block]} />);

    const image = await screen.findByAltText('一只红狐狸');
    expect(getFileUrlMock).toHaveBeenCalledWith('file-1', 'processed');
    expect(image).toHaveAttribute('src', '/api/files/file-1/content?variant=processed&token=t');
    expect(screen.getByText('一只红狐狸', { selector: 'figcaption' })).toBeInTheDocument();

    fireEvent.click(image);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});
