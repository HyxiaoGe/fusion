'use client';

import { memo, useState } from 'react';
import type { FileBlock, GeneratedImageBlock } from '@/types/conversation';
import AuthImage from './AuthImage';
import ImageViewer from './ImageViewer';

interface GeneratedImagesProps {
  blocks: GeneratedImageBlock[];
}

function toFileBlock(block: GeneratedImageBlock): FileBlock {
  return {
    type: 'file',
    id: block.id,
    file_id: block.file_id,
    filename: block.prompt,
    mime_type: block.mime_type,
    ...(block.width ? { width: block.width } : {}),
    ...(block.height ? { height: block.height } : {}),
  };
}

/** 模型生成的图片：按 file_id 取签名地址展示，点击打开原图查看器。 */
function GeneratedImages({ blocks }: GeneratedImagesProps) {
  const [viewing, setViewing] = useState<FileBlock | null>(null);
  if (blocks.length === 0) return null;

  return (
    <div className="mb-4 flex w-full max-w-6xl flex-wrap gap-3" data-testid="generated-images">
      {blocks.map(block => (
        <figure key={block.id} className="m-0 flex max-w-[min(100%,512px)] flex-col gap-1.5">
          <AuthImage
            fileId={block.file_id}
            alt={block.prompt}
            variant="processed"
            className="max-h-[512px] w-auto max-w-full cursor-zoom-in rounded-lg border border-border/60 object-contain"
            onClick={() => setViewing(toFileBlock(block))}
          />
          <figcaption className="line-clamp-2 text-xs text-muted-foreground" title={block.prompt}>
            {block.prompt}
          </figcaption>
        </figure>
      ))}
      <ImageViewer fileBlock={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

export default memo(GeneratedImages);
