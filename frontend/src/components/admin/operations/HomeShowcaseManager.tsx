'use client';

import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ImageMinus, ImagePlus } from 'lucide-react';
import { AdminEmpty, AdminError, AdminLoading, AdminPanelHeader } from '@/components/admin/AdminPanelPrimitives';
import {
  SettingsButton as Button,
  SettingsFieldError,
  SettingsInput as Input,
  SettingsTextarea as Textarea,
} from '@/components/settings/SettingsControls';
import { Label } from '@/components/ui/label';
import {
  addShowcaseFromGenerated,
  deleteShowcaseImage,
  fetchAdminShowcaseImages,
  fetchGeneratedShowcaseCandidates,
  type GeneratedShowcaseCandidate,
  type GeneratedShowcaseGroup,
  type ShowcaseImage,
} from '@/lib/api/prompts';
import { cn } from '@/lib/utils';

const LABEL_MAX = 12;
const PROMPT_MAX = 1000;
const POSITION_OPTIONS: Array<{ value: '' | 'top' | 'bottom'; label: string }> = [
  { value: '', label: '居中' },
  { value: 'top', label: '保留顶部' },
  { value: 'bottom', label: '保留底部' },
];

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/** 首页生图示例卡片的图片池：首页每次进入时打乱顺序、每次展示 4 张，点击把提示词填入输入框。
 * 图片从当前管理员自己生成过的图里挑（默认沿用生成时的名称和提示词）；下架即移出图片池，图回到挑选列表。 */
export default function HomeShowcaseManager() {
  const [images, setImages] = useState<ShowcaseImage[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [groups, setGroups] = useState<GeneratedShowcaseGroup[] | null>(null);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [candidatesOffset, setCandidatesOffset] = useState<number | null>(null);
  const [loadingMoreCandidates, setLoadingMoreCandidates] = useState(false);
  const [hideInPool, setHideInPool] = useState(true);
  const [expandedGroupKey, setExpandedGroupKey] = useState<string | null>(null);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [position, setPosition] = useState<'' | 'top' | 'bottom'>('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // 切换筛选后旧请求才返回时丢弃，避免覆盖新列表
  const candidatesRequestRef = useRef(0);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const data = await fetchAdminShowcaseImages();
      setImages(data.images);
    } catch (error) {
      setLoadError(errorMessage(error, '图片池加载失败'));
    }
  }, []);

  // quiet：图片池变动后原地刷新，不清空列表、不收起展开的组
  const loadCandidates = useCallback(async ({ quiet = false } = {}) => {
    const request = ++candidatesRequestRef.current;
    setCandidatesError(null);
    if (!quiet) {
      setGroups(null);
      setExpandedGroupKey(null);
    }
    try {
      const data = await fetchGeneratedShowcaseCandidates({ hideInPool });
      if (request !== candidatesRequestRef.current) return;
      setGroups(data.groups);
      setCandidatesOffset(data.next_offset);
    } catch (error) {
      if (request !== candidatesRequestRef.current) return;
      setCandidatesError(errorMessage(error, '生成图列表加载失败'));
    }
  }, [hideInPool]);

  const loadMoreCandidates = async () => {
    if (candidatesOffset === null || loadingMoreCandidates) return;
    const request = candidatesRequestRef.current;
    setLoadingMoreCandidates(true);
    setCandidatesError(null);
    try {
      const data = await fetchGeneratedShowcaseCandidates({ offset: candidatesOffset, hideInPool });
      if (request !== candidatesRequestRef.current) return;
      setGroups((current) => {
        // 两次请求之间图片池有变动时，分页边界可能挪动，同一组只留一份
        const known = new Set((current ?? []).map((group) => group.key));
        return [...(current ?? []), ...data.groups.filter((group) => !known.has(group.key))];
      });
      setCandidatesOffset(data.next_offset);
    } catch (error) {
      setCandidatesError(errorMessage(error, '生成图列表加载失败'));
    } finally {
      setLoadingMoreCandidates(false);
    }
  };

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadCandidates();
  }, [loadCandidates]);

  const markInPool = (fileId: string, inPool: boolean) => {
    setGroups((current) => current?.map((group) => ({
      ...group,
      versions: group.versions.map((candidate) => (
        candidate.file_id === fileId ? { ...candidate, in_pool: inPool } : candidate
      )),
    })) ?? null);
  };

  // 隐藏已在图片池的图时，刚加进池的版本也立刻收起；整组都在池里就不再显示这组
  const visibleGroups = (groups ?? [])
    .map((group) => ({
      ...group,
      versions: hideInPool ? group.versions.filter((candidate) => !candidate.in_pool) : group.versions,
    }))
    .filter((group) => group.versions.length > 0);

  const selectCandidate = (candidate: GeneratedShowcaseCandidate) => {
    setSelectedFileId(candidate.file_id);
    setLabel((candidate.title ?? '').slice(0, LABEL_MAX));
    setPrompt(candidate.prompt.slice(0, PROMPT_MAX));
  };

  const renderCandidate = (candidate: GeneratedShowcaseCandidate) => {
    const selected = candidate.file_id === selectedFileId;
    return (
      <button
        type="button"
        aria-pressed={selected}
        aria-label={candidate.prompt.slice(0, 40) || '生成图'}
        title={candidate.prompt}
        onClick={() => selectCandidate(candidate)}
        className={cn(
          'relative block aspect-[4/5] w-full overflow-hidden rounded-lg border bg-muted',
          selected ? 'border-primary ring-2 ring-primary' : 'border-border/60',
        )}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- 签名地址的缩略图，原样展示 */}
        <img src={candidate.thumbnail_url} alt="" loading="lazy" className="h-full w-full object-cover" />
        {candidate.in_pool ? (
          <span className="absolute inset-x-0 bottom-0 bg-black/60 py-0.5 text-center text-[11px] text-white">已在图片池</span>
        ) : null}
      </button>
    );
  };

  const canSubmit = Boolean(selectedFileId) && label.trim().length > 0 && prompt.trim().length > 0 && !submitting;

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setFormError(null);
    const fields = { label: label.trim(), prompt: prompt.trim(), objectPosition: position || null };
    try {
      const created = await addShowcaseFromGenerated({ fileId: selectedFileId ?? '', ...fields });
      setImages((current) => [...(current ?? []), created]);
      if (created.source_file_id) markInPool(created.source_file_id, true);
      setSelectedFileId(null);
      setLabel('');
      setPrompt('');
      setPosition('');
    } catch (error) {
      setFormError(errorMessage(error, '添加失败'));
    } finally {
      setSubmitting(false);
    }
  };

  // 下架即移出图片池；原图仍在生成图里，刷新挑选列表让它回来
  const handleUnpublish = async (id: string) => {
    if (pendingDeleteId !== id) {
      setPendingDeleteId(id);
      return;
    }
    setDeletingId(id);
    try {
      await deleteShowcaseImage(id);
      setImages((current) => (current ?? []).filter((image) => image.id !== id));
      setPendingDeleteId(null);
      void loadCandidates({ quiet: true });
    } catch (error) {
      setLoadError(errorMessage(error, '下架失败'));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <AdminPanelHeader
        title="首页示例图"
        description="首页「生图示例」卡片的图片池，每次进首页打乱顺序，每次展示 4 张、6 秒轮换；用户点击后把提示词填入输入框；下架后图回到挑选列表。只对开通生图的用户展示。"
      />

      <form onSubmit={(event) => void handleSubmit(event)} className="grid gap-4 rounded-xl border border-border/60 p-4 sm:grid-cols-2">
        <fieldset className="space-y-2 sm:col-span-2">
          <legend className="text-sm font-medium">从我的生成图挑选</legend>
          <div>
            <label className="mb-2 flex w-fit items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={hideInPool} onChange={(event) => setHideInPool(event.target.checked)} />
              隐藏已在图片池的图
            </label>
            {candidatesError ? <AdminError message={candidatesError} onRetry={() => void loadCandidates()} /> : null}
            {groups === null && !candidatesError ? <AdminLoading /> : null}
            {groups !== null && visibleGroups.length === 0 && candidatesOffset === null ? (
              <AdminEmpty>{hideInPool ? '没有不在图片池里的生成图' : '你还没有生成过图片'}</AdminEmpty>
            ) : null}
            {groups !== null && (visibleGroups.length > 0 || candidatesOffset !== null) ? (
              // 展开的版本行占整行；dense 让同一行剩下的卡片回填到它前面，不留空位
              <ul aria-label="我的生成图" className="grid max-h-[420px] grid-flow-row-dense grid-cols-3 gap-2 overflow-y-auto pr-1 sm:grid-cols-5 lg:grid-cols-7">
                {visibleGroups.map((group) => {
                  const expanded = group.key === expandedGroupKey;
                  return (
                    <Fragment key={group.key}>
                      <li className="relative">
                        {renderCandidate(group.versions[0])}
                        {group.versions.length > 1 ? (
                          <button
                            type="button"
                            aria-expanded={expanded}
                            onClick={() => setExpandedGroupKey(expanded ? null : group.key)}
                            className={cn(
                              'absolute right-1 top-1 rounded-full px-1.5 py-0.5 text-[11px] text-white',
                              expanded ? 'bg-primary' : 'bg-black/60 hover:bg-black/75',
                            )}
                          >
                            {group.versions.length} 个版本
                          </button>
                        ) : null}
                      </li>
                      {expanded ? (
                        <li className="col-span-full rounded-lg bg-muted/60 p-2">
                          <ul aria-label="同一张图的各个版本" className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7">
                            {group.versions.map((candidate) => (
                              <li key={candidate.file_id}>{renderCandidate(candidate)}</li>
                            ))}
                          </ul>
                          <p className="mt-1.5 text-xs text-muted-foreground">这些图由同一张图多次编辑或重画得到，新的在前。</p>
                        </li>
                      ) : null}
                    </Fragment>
                  );
                })}
                {candidatesOffset !== null ? (
                  <li className="col-span-full flex justify-center py-1">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={loadingMoreCandidates}
                      onClick={() => void loadMoreCandidates()}
                    >
                      {loadingMoreCandidates ? '加载中…' : '加载更早的图'}
                    </Button>
                  </li>
                ) : null}
              </ul>
            ) : null}
            <p className="mt-1.5 text-xs text-muted-foreground">只列你自己生成过的图，同一张图多次编辑的版本合成一组、显示最新的一张；选中后标签和提示词默认用生成时的名称和原文，可以改。</p>
          </div>
        </fieldset>
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
                loading="lazy"
                decoding="async"
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
                  onClick={() => void handleUnpublish(image.id)}
                  onBlur={() => setPendingDeleteId((current) => (current === image.id ? null : current))}
                  aria-label={pendingDeleteId === image.id ? `确认下架${image.label}` : `下架${image.label}`}
                >
                  <ImageMinus aria-hidden="true" />
                  {pendingDeleteId === image.id ? '确认下架' : '下架'}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
