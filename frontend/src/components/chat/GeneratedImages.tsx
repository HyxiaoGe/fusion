'use client';

import { memo, useCallback, useEffect, useState, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, ImageOff, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { getFileUrl } from '@/lib/api/files';
import type { FileBlock, GeneratedImageBlock } from '@/types/conversation';
import ImageViewer from './ImageViewer';
import {
  imageDisplayWidth,
  imageModelDisplayName,
  parseAspectRatio,
  type PendingImageGeneration,
} from './generatedImageModel';

interface GeneratedImagesProps {
  blocks: GeneratedImageBlock[];
  pending?: PendingImageGeneration[];
  /** 替用户发一条「同样描述再来一张」；只在可以继续对话时提供。 */
  onRegenerate?: (request: string) => void;
}

const EMPTY_PENDING: PendingImageGeneration[] = [];
const KNOWN_FAILURE_CODES = new Set(['content_filtered', 'upstream_timeout', 'generation_in_progress']);

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

function frameStyle(width: number, height: number): CSSProperties {
  return { width: imageDisplayWidth(width, height), aspectRatio: `${width} / ${height}` };
}

function blockFrame(block: GeneratedImageBlock): [number, number] {
  if (block.width && block.height) return [block.width, block.height];
  return parseAspectRatio(block.aspect_ratio);
}

function downloadName(block: GeneratedImageBlock): string {
  const extension = block.mime_type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
  return `fusion-image-${block.file_id.slice(0, 8)}.${extension}`;
}

/** 点阵占位：亮区随耗时扩散（不表示真实进度，只让等待有渐进感）。 */
function ImageDots({ elapsedSeconds, glow }: { elapsedSeconds: number; glow: boolean }) {
  const reveal = 70 + 150 * (1 - Math.exp(-elapsedSeconds / 20));
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div className="image-gen-dots" />
      <div
        className="image-gen-dots-glow"
        style={{ '--image-gen-reveal': `${reveal}%`, opacity: glow ? undefined : 0 } as CSSProperties}
      />
    </div>
  );
}

function useElapsedSeconds(startedAt: number, active: boolean, completedAt?: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const end = active ? now : (completedAt ?? now);
  return Math.max(0, Math.floor((end - startedAt) / 1000));
}

function PendingImageCard({ pending }: { pending: PendingImageGeneration }) {
  const { t } = useTranslation();
  const running = pending.status === 'running';
  const elapsedSeconds = useElapsedSeconds(pending.startedAt, running, pending.completedAt);
  const [width, height] = parseAspectRatio(pending.aspectRatio);
  const failureCode = pending.errorCode && KNOWN_FAILURE_CODES.has(pending.errorCode) ? pending.errorCode : 'default';
  const label = running
    ? t('chatBody.generatedImage.creating')
    : pending.status === 'interrupted'
      ? t('chatBody.generatedImage.interrupted')
      : t(`chatBody.generatedImage.failed.${failureCode}`);

  return (
    <figure
      className="m-0 flex max-w-full flex-col gap-1.5"
      style={{ width: frameStyle(width, height).width }}
      data-testid="generated-image-pending"
      data-status={pending.status}
    >
      <div
        className="relative max-w-full overflow-hidden rounded-xl border border-border/60 bg-muted/30"
        style={frameStyle(width, height)}
        role={running ? 'status' : undefined}
        aria-live={running ? 'polite' : undefined}
      >
        <ImageDots elapsedSeconds={elapsedSeconds} glow={running} />
        <span
          className={running
            ? 'absolute left-3 top-2.5 text-xs font-medium text-muted-foreground'
            : 'absolute inset-x-3 top-1/2 -translate-y-1/2 text-center text-sm text-muted-foreground'}
        >
          {label}
        </span>
        {running ? (
          <span className="absolute bottom-2.5 right-3 rounded-full bg-background/80 px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground shadow-sm backdrop-blur">
            {t('chatBody.generatedImage.elapsed', { seconds: elapsedSeconds })}
          </span>
        ) : null}
      </div>
    </figure>
  );
}

function GeneratedImageCard({
  block,
  onOpen,
  onRegenerate,
}: {
  block: GeneratedImageBlock;
  onOpen: () => void;
  onRegenerate?: (request: string) => void;
}) {
  const { t } = useTranslation();
  const [src, setSrc] = useState('');
  const [state, setState] = useState<'loading' | 'ready' | 'loaded' | 'failed'>('loading');
  const [promptOpen, setPromptOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [width, height] = blockFrame(block);
  const modelName = imageModelDisplayName(block.model);

  const load = useCallback(async () => {
    setState('loading');
    try {
      setSrc(await getFileUrl(block.file_id, 'processed'));
      setState('ready');
    } catch {
      setState('failed');
    }
  }, [block.file_id]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const url = src || await getFileUrl(block.file_id, 'processed');
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(String(response.status));
        const objectUrl = URL.createObjectURL(await response.blob());
        const anchor = document.createElement('a');
        anchor.href = objectUrl;
        anchor.download = downloadName(block);
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      } catch {
        window.open(url, '_blank', 'noopener');
      }
    } finally {
      setDownloading(false);
    }
  };

  return (
    <figure className="m-0 flex max-w-full flex-col gap-1.5" style={{ width: frameStyle(width, height).width }}>
      <div
        className="relative max-w-full overflow-hidden rounded-xl border border-border/60 bg-muted/30"
        style={frameStyle(width, height)}
      >
        {state !== 'loaded' && state !== 'failed' ? <ImageDots elapsedSeconds={0} glow /> : null}
        {state === 'failed' ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <ImageOff className="h-5 w-5" aria-hidden />
            <span>{t('chatBody.generatedImage.loadFailed')}</span>
            <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {t('chatBody.generatedImage.retryLoad')}
            </Button>
          </div>
        ) : null}
        {src && state !== 'failed' ? (
          // eslint-disable-next-line @next/next/no-img-element -- 签名地址的私有文件，不走 next/image 优化
          <img
            src={src}
            alt={block.prompt}
            data-loaded={state === 'loaded'}
            className="image-gen-image absolute inset-0 h-full w-full cursor-zoom-in object-cover"
            onLoad={() => setState('loaded')}
            onError={() => setState('failed')}
            onClick={onOpen}
          />
        ) : null}
      </div>
      <figcaption className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
        <div className="flex min-w-0 items-center gap-1">
          {modelName ? (
            <span className="min-w-0 flex-1 truncate">
              {t('chatBody.generatedImage.providedBy', { model: modelName })}
            </span>
          ) : <span className="flex-1" />}
          <button
            type="button"
            className="shrink-0 rounded px-1.5 py-0.5 hover:bg-muted hover:text-foreground"
            aria-expanded={promptOpen}
            onClick={() => setPromptOpen(open => !open)}
          >
            {promptOpen ? t('chatBody.generatedImage.hidePrompt') : t('chatBody.generatedImage.showPrompt')}
          </button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted-foreground"
            aria-label={t('chatBody.generatedImage.download')}
            title={t('chatBody.generatedImage.download')}
            disabled={downloading}
            onClick={() => void handleDownload()}
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
          </Button>
          {onRegenerate ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 shrink-0 text-muted-foreground"
              aria-label={t('chatBody.generatedImage.regenerate')}
              title={t('chatBody.generatedImage.regenerate')}
              onClick={() => onRegenerate(t('chatBody.generatedImage.regenerateRequest'))}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            </Button>
          ) : null}
        </div>
        {promptOpen ? (
          <p className="m-0 whitespace-pre-wrap rounded-md bg-muted/50 px-2 py-1.5 leading-relaxed">{block.prompt}</p>
        ) : null}
      </figcaption>
    </figure>
  );
}

/** 模型生成的图片：生成中按目标比例显示点阵占位，出图后原位淡入，卡片下标注生图模型。 */
function GeneratedImages({ blocks, pending = EMPTY_PENDING, onRegenerate }: GeneratedImagesProps) {
  const [viewing, setViewing] = useState<FileBlock | null>(null);
  if (blocks.length === 0 && pending.length === 0) return null;

  return (
    <div className="mb-4 flex w-full max-w-6xl flex-wrap items-start gap-3" data-testid="generated-images">
      {blocks.map(block => (
        <GeneratedImageCard
          key={block.id}
          block={block}
          onOpen={() => setViewing(toFileBlock(block))}
          onRegenerate={onRegenerate}
        />
      ))}
      {pending.map(item => <PendingImageCard key={item.toolCallId} pending={item} />)}
      <ImageViewer fileBlock={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

export default memo(GeneratedImages);
