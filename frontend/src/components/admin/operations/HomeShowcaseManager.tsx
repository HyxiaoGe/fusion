'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ImagePlus, Trash2 } from 'lucide-react';
import { AdminEmpty, AdminError, AdminLoading, AdminPanelHeader } from '@/components/admin/AdminPanelPrimitives';
import {
  SettingsButton as Button,
  SettingsFieldError,
  SettingsInput as Input,
  SettingsTextarea as Textarea,
} from '@/components/settings/SettingsControls';
import { Label } from '@/components/ui/label';
import {
  deleteShowcaseImage,
  fetchAdminShowcaseImages,
  uploadShowcaseImage,
  type ShowcaseImage,
} from '@/lib/api/prompts';

const LABEL_MAX = 12;
const PROMPT_MAX = 500;
const POSITION_OPTIONS: Array<{ value: '' | 'top' | 'bottom'; label: string }> = [
  { value: '', label: '居中' },
  { value: 'top', label: '保留顶部' },
  { value: 'bottom', label: '保留底部' },
];

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** 首页生图示例卡片的图片池：首页按添加顺序每次展示 4 张，点击把提示词填入输入框。 */
export default function HomeShowcaseManager() {
  const [images, setImages] = useState<ShowcaseImage[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [position, setPosition] = useState<'' | 'top' | 'bottom'>('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const data = await fetchAdminShowcaseImages();
      setImages(data.images);
    } catch (error) {
      setLoadError(errorMessage(error, '图片池加载失败'));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canSubmit = Boolean(file) && label.trim().length > 0 && prompt.trim().length > 0 && !submitting;

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !canSubmit) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await uploadShowcaseImage({
        file,
        label: label.trim(),
        prompt: prompt.trim(),
        objectPosition: position || null,
      });
      setImages((current) => [...(current ?? []), created]);
      setFile(null);
      setLabel('');
      setPrompt('');
      setPosition('');
      if (fileInputRef.current) fileInputRef.current.value = '';
    } catch (error) {
      setFormError(errorMessage(error, '添加失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (pendingDeleteId !== id) {
      setPendingDeleteId(id);
      return;
    }
    setDeletingId(id);
    try {
      await deleteShowcaseImage(id);
      setImages((current) => (current ?? []).filter((image) => image.id !== id));
      setPendingDeleteId(null);
    } catch (error) {
      setLoadError(errorMessage(error, '删除失败'));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <AdminPanelHeader
        title="首页示例图"
        description="首页「生图示例」卡片的图片池，按添加顺序每次展示 4 张、6 秒轮换；用户点击后把提示词填入输入框。只对开通生图的用户展示。"
      />

      <form onSubmit={(event) => void handleSubmit(event)} className="grid gap-4 rounded-xl border border-border/60 p-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="showcase-file">图片</Label>
          <Input
            id="showcase-file"
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <p className="text-xs text-muted-foreground">卡片按 4:5 裁切，竖图效果最好；上传后自动压缩成 WebP。</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="showcase-label">标签</Label>
          <Input
            id="showcase-label"
            value={label}
            maxLength={LABEL_MAX}
            placeholder="如：水彩插画"
            onChange={(event) => setLabel(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">卡片左下角的风格名，最多 {LABEL_MAX} 个字。</p>
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="showcase-prompt">做同款提示词</Label>
          <Textarea
            id="showcase-prompt"
            value={prompt}
            maxLength={PROMPT_MAX}
            rows={3}
            placeholder="用户点击卡片后填入输入框的内容"
            onChange={(event) => setPrompt(event.target.value)}
          />
        </div>
        <fieldset className="space-y-1.5">
          <legend className="text-sm font-medium">裁切位置</legend>
          <div className="flex gap-4 text-sm">
            {POSITION_OPTIONS.map((option) => (
              <label key={option.value || 'center'} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="showcase-position"
                  value={option.value}
                  checked={position === option.value}
                  onChange={() => setPosition(option.value)}
                />
                {option.label}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex items-end justify-end gap-3">
          {formError ? <SettingsFieldError>{formError}</SettingsFieldError> : null}
          <Button type="submit" size="sm" disabled={!canSubmit}>
            <ImagePlus aria-hidden="true" />
            {submitting ? '添加中…' : '添加到图片池'}
          </Button>
        </div>
      </form>

      {loadError ? <AdminError message={loadError} onRetry={() => void load()} /> : null}
      {images === null && !loadError ? <AdminLoading /> : null}
      {images?.length === 0 ? <AdminEmpty>图片池是空的，首页不会展示生图示例</AdminEmpty> : null}
      {images && images.length > 0 ? (
        <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4" aria-label="图片池">
          {images.map((image) => (
            <li key={image.id} className="overflow-hidden rounded-xl border border-border/60">
              {/* eslint-disable-next-line @next/next/no-img-element -- 管理页预览，原样展示已压缩的图 */}
              <img
                src={image.image_url}
                alt={image.prompt}
                className="aspect-[4/5] w-full bg-muted object-cover"
                style={image.object_position ? { objectPosition: image.object_position } : undefined}
              />
              <div className="space-y-2 p-3">
                <div className="text-sm font-medium">{image.label}</div>
                <p className="line-clamp-3 text-xs text-muted-foreground">{image.prompt}</p>
                <Button
                  size="sm"
                  variant={pendingDeleteId === image.id ? 'destructive' : 'outline'}
                  disabled={deletingId === image.id}
                  onClick={() => void handleDelete(image.id)}
                  onBlur={() => setPendingDeleteId((current) => (current === image.id ? null : current))}
                  aria-label={pendingDeleteId === image.id ? `确认删除${image.label}` : `删除${image.label}`}
                >
                  <Trash2 aria-hidden="true" />
                  {pendingDeleteId === image.id ? '确认删除' : '删除'}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
