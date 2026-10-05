'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Check, ChevronsUpDown, Filter, History, RefreshCw } from 'lucide-react';
import {
  getAdminConversation,
  getAdminConversationAgentRuns,
  getAdminConversationFiles,
  getAdminConversationMessages,
  getAdminConversations,
  getAdminConversationToolCalls,
  getAdminModel,
  getAdminModels,
} from '@/lib/api/adminAudit';
import { useAdminAuditResource } from '@/hooks/useAdminAuditResource';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import ProviderIcon from '@/components/models/ProviderIcon';
import type { AdminConversationsQuery, AdminModelSummary } from '@/types/adminAudit';
import { cn } from '@/lib/utils';
import AdminExecutionInspector from './AdminExecutionInspector';
import AdminMessageCard from './AdminMessageCard';
import AdminUserIdentity from './AdminUserIdentity';
import {
  AdminAgentStatus, AdminEmpty, AdminError, AdminFilterActions, AdminFilterField, AdminLoading, AdminPagination, AdminPanelHeader, formatAdminDate, formatNumber,
} from './AdminPanelPrimitives';
import { normalizeAdminAuditRouteId } from '@/lib/admin/adminAuditRoute';
import { isAdminAccessError } from '@/lib/admin/adminAccess';
import styles from './AdminSurface.module.css';

interface ConversationFilterDraft {
  q: string;
  user_id: string;
  model_id: string;
  has_tools: '' | 'true' | 'false';
  has_files: '' | 'true' | 'false';
  created_from: string;
  created_to: string;
}

const EMPTY_FILTER: ConversationFilterDraft = { q: '', user_id: '', model_id: '', has_tools: '', has_files: '', created_from: '', created_to: '' };

interface AdminConversationsPanelProps {
  active?: boolean;
  onForbidden: () => void;
  userIdFilter?: string;
  modelIdFilter?: string;
  selectedConversationId: string | null;
  onUserFilterChange: (userId?: string) => void;
  onFiltersChange?: (filters: { userId?: string; modelId?: string }) => void;
  onOpen: (conversationId: string) => void;
  onBack: () => void;
}

export default function AdminConversationsPanel({
  active = true, onForbidden, userIdFilter, modelIdFilter, selectedConversationId, onUserFilterChange, onFiltersChange, onOpen, onBack,
}: AdminConversationsPanelProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [draft, setDraft] = useState<ConversationFilterDraft>(() => ({ ...EMPTY_FILTER, user_id: userIdFilter ?? '', model_id: modelIdFilter ?? '' }));
  const [filters, setFilters] = useState<AdminConversationsQuery>(() => ({ ...(userIdFilter ? { user_id: userIdFilter } : {}), ...(modelIdFilter ? { model_id: modelIdFilter } : {}) }));
  const previousUserIdFilterRef = useRef(userIdFilter);
  const previousModelIdFilterRef = useRef(modelIdFilter);
  const loader = useCallback(
    (signal: AbortSignal) => getAdminConversations({ page, page_size: 25, ...filters }, signal),
    [filters, page],
  );
  const resource = useAdminAuditResource(loader, onForbidden);

  useEffect(() => {
    if (previousUserIdFilterRef.current === userIdFilter) return;
    previousUserIdFilterRef.current = userIdFilter;
    const nextUserId = userIdFilter ?? '';
    setPage(1);
    setDraft(current => current.user_id === nextUserId ? current : { ...current, user_id: nextUserId });
    setFilters(current => current.user_id === (nextUserId || undefined)
      ? current
      : { ...current, user_id: nextUserId || undefined });
  }, [userIdFilter]);

  useEffect(() => {
    if (previousModelIdFilterRef.current === modelIdFilter) return;
    previousModelIdFilterRef.current = modelIdFilter;
    const nextModelId = modelIdFilter ?? '';
    setPage(1);
    setDraft(current => current.model_id === nextModelId ? current : { ...current, model_id: nextModelId });
    setFilters(current => current.model_id === (nextModelId || undefined) ? current : { ...current, model_id: nextModelId || undefined });
  }, [modelIdFilter]);

  if (selectedConversationId) {
    return (
      <AdminConversationDetailView
        key={selectedConversationId}
        conversationId={selectedConversationId}
        onBack={onBack}
        onForbidden={onForbidden}
      />
    );
  }

  const applyFilters = (event: React.FormEvent) => {
    event.preventDefault();
    const nextUserId = normalizeAdminAuditRouteId(draft.user_id);
    const nextModelId = normalizeAdminAuditRouteId(draft.model_id);
    setPage(1);
    setFilters({
      q: draft.q,
      user_id: nextUserId,
      model_id: nextModelId,
      has_tools: parseBoolean(draft.has_tools),
      has_files: parseBoolean(draft.has_files),
      created_from: draft.created_from,
      created_to: draft.created_to,
    });
    if (onFiltersChange) onFiltersChange({ userId: nextUserId, modelId: nextModelId });
    else onUserFilterChange(nextUserId);
  };

  const resetFilters = () => {
    setPage(1);
    setDraft({ ...EMPTY_FILTER });
    setFilters({});
    if (onFiltersChange) onFiltersChange({ userId: undefined, modelId: undefined });
    else onUserFilterChange(undefined);
  };

  return (
    <section>
      <AdminPanelHeader title="全局对话" description="跨用户检索已持久化对话；管理页不会续写、停止或修改内容。" action={<Button variant="outline" size="sm" onClick={resource.reload} aria-label="刷新对话列表"><RefreshCw />刷新</Button>} />
      <form onSubmit={applyFilters} className={`${styles.filters} ${styles.conversationFilters}`}>
        <AdminFilterField label={t('admin.filters.conversationSearch')} className={styles.wideField}><Input aria-label={t('admin.filters.conversationSearch')} value={draft.q} onChange={event => setDraft(current => ({ ...current, q: event.target.value }))} placeholder="标题或用户关键词" /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.userId')}><Input aria-label={t('admin.filters.userId')} value={draft.user_id} onChange={event => setDraft(current => ({ ...current, user_id: event.target.value }))} placeholder="用户 ID" /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.model')}>
        <AdminModelFilterCombobox
          active={active}
          value={draft.model_id}
          onForbidden={onForbidden}
          onChange={modelId => setDraft(current => ({ ...current, model_id: modelId }))}
        />
        </AdminFilterField>
        <AdminFilterField label={t('admin.filters.tools')}><select aria-label={t('admin.filters.tools')} className="px-3 text-sm" value={draft.has_tools} onChange={event => setDraft(current => ({ ...current, has_tools: event.target.value as ConversationFilterDraft['has_tools'] }))}><option value="">工具不限</option><option value="true">有工具</option><option value="false">无工具</option></select></AdminFilterField>
        <AdminFilterField label={t('admin.filters.files')}><select aria-label={t('admin.filters.files')} className="px-3 text-sm" value={draft.has_files} onChange={event => setDraft(current => ({ ...current, has_files: event.target.value as ConversationFilterDraft['has_files'] }))}><option value="">文件不限</option><option value="true">有文件</option><option value="false">无文件</option></select></AdminFilterField>
        <AdminFilterField label={t('admin.filters.createdFrom')}><Input aria-label={t('admin.filters.createdFrom')} type="date" value={draft.created_from} onChange={event => setDraft(current => ({ ...current, created_from: event.target.value }))} /></AdminFilterField>
        <AdminFilterField label={t('admin.filters.createdTo')}><Input aria-label={t('admin.filters.createdTo')} type="date" value={draft.created_to} onChange={event => setDraft(current => ({ ...current, created_to: event.target.value }))} /></AdminFilterField>
        <AdminFilterActions className={styles.filterActions} submitLabel="应用筛选" submitIcon={<Filter />} onReset={resetFilters} />
      </form>

      {resource.loading ? <AdminLoading /> : resource.error ? <AdminError message={resource.error} onRetry={resource.reload} /> : null}
      {resource.data && resource.data.items.length === 0 ? <AdminEmpty>没有匹配的对话</AdminEmpty> : null}
      {resource.data && resource.data.items.length > 0 ? (
        <>
          <div className={styles.tableFrame} role="region" aria-label={t('admin.conversationsList')} tabIndex={0}>
            <table className={`${styles.table} min-w-[1280px]`}>
              <colgroup><col style={{ width: '25%' }} /><col style={{ width: '21%' }} /><col style={{ width: '14%' }} /><col style={{ width: '5%' }} /><col style={{ width: '5%' }} /><col style={{ width: '5%' }} /><col style={{ width: '10%' }} /><col style={{ width: '7%' }} /><col style={{ width: '8%' }} /></colgroup>
              <thead><tr><th scope="col">对话 / 时间</th><th scope="col">用户</th><th scope="col">模型</th><th scope="col" className={styles.numeric}>消息</th><th scope="col" className={styles.numeric}>工具</th><th scope="col" className={styles.numeric}>文件</th><th scope="col">Agent</th><th scope="col" className={styles.numeric}>Token</th><th scope="col" className={styles.actions}>操作</th></tr></thead>
              <tbody>{resource.data.items.map(conversation => (
                <tr key={conversation.id}>
                  <td>
                    <div className={styles.recordTitle} title={conversation.title}>{conversation.title || '未命名对话'}</div>
                    <div className={`mt-1 ${styles.recordId}`} title={conversation.id}>{conversation.id}</div>
                    <div className={styles.recordTime} aria-label={`对话时间 ${conversation.id}`}>
                      {conversation.created_at || conversation.updated_at ? (
                        <><span>更新：{formatAdminDate(conversation.updated_at)}</span><span>创建：{formatAdminDate(conversation.created_at)}</span></>
                      ) : '时间未记录'}
                    </div>
                  </td>
                  <td><AdminUserIdentity user={conversation.user} compact /></td>
                  <td className={styles.modelId}>{conversation.model_id || '—'}</td>
                  <td className={styles.numeric}>{formatNumber(conversation.message_count)}</td><td className={styles.numeric}>{formatNumber(conversation.tool_call_count)}</td><td className={styles.numeric}>{formatNumber(conversation.file_count)}</td>
                  <td>{conversation.latest_agent_status ? <AdminAgentStatus status={conversation.latest_agent_status} /> : '—'}</td>
                  <td className={styles.numeric}>{formatNumber(conversation.input_tokens + conversation.output_tokens)}</td>
                  <td className={styles.actions}><Button variant="ghost" size="sm" aria-label={`查看对话详情 ${conversation.id}`} onClick={() => onOpen(conversation.id)}>查看详情</Button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <AdminPagination page={resource.data} onPageChange={setPage} />
        </>
      ) : null}
    </section>
  );
}

interface ModelFilterOption {
  id: string;
  name: string;
  providerId: string;
  providerLabel: string;
  historical: boolean;
  unresolved: boolean;
}

function AdminModelFilterCombobox({
  active,
  value,
  onForbidden,
  onChange,
}: {
  active: boolean;
  value: string;
  onForbidden: () => void;
  onChange: (modelId: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [models, setModels] = useState<AdminModelSummary[]>([]);
  const [knownModels, setKnownModels] = useState<AdminModelSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [activeOptionId, setActiveOptionId] = useState<string | null>(null);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedQuery(query.trim().slice(0, 200));
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    if (!active) {
      setOpen(false);
      setQuery('');
      setDebouncedQuery('');
      setActiveOptionId(null);
    }
  }, [active]);

  useEffect(() => {
    if (!value) return;
    const controller = new AbortController();
    getAdminModel(value, controller.signal)
      .then(model => {
        if (controller.signal.aborted) return;
        setKnownModels(current => mergeKnownModels(current, [model]));
      })
      .catch(reason => {
        if (controller.signal.aborted) return;
        if (isAdminAccessError(reason)) onForbidden();
      });
    return () => controller.abort();
  }, [onForbidden, value]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getAdminModels({ page: 1, page_size: 100, q: debouncedQuery }, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        setModels(page.items);
        setKnownModels(current => mergeKnownModels(current, page.items));
        setLoading(false);
      })
      .catch(reason => {
        if (controller.signal.aborted) return;
        setModels([]);
        setLoading(false);
        if (isAdminAccessError(reason)) {
          onForbidden();
          return;
        }
        setError(reason instanceof Error ? reason.message : '模型目录读取失败');
      });
    return () => controller.abort();
  }, [debouncedQuery, onForbidden, open, revision]);

  const selectedModel = value
    ? knownModels.find(model => model.model_id === value) || models.find(model => model.model_id === value)
    : undefined;
  const selected = value ? toModelFilterOption(selectedModel, value) : undefined;
  const normalizedQuery = debouncedQuery.toLocaleLowerCase();
  const resultOptions = useMemo(() => models.map(model => toModelFilterOption(model, model.model_id)), [models]);
  const selectedInResults = value && resultOptions.some(option => option.id === value);
  const selectedMatchesQuery = selected && (
    !normalizedQuery
    || selected.name.toLocaleLowerCase().includes(normalizedQuery)
    || selected.id.toLocaleLowerCase().includes(normalizedQuery)
  );
  const options = selected && !selectedInResults && selectedMatchesQuery
    ? [selected, ...resultOptions]
    : resultOptions;
  const allOptionOffset = debouncedQuery ? 0 : 1;
  const visibleOptionIds = [...(allOptionOffset ? [''] : []), ...options.map(option => option.id)];
  const activeOptionIndex = activeOptionId === null ? -1 : visibleOptionIds.indexOf(activeOptionId);

  useEffect(() => {
    setActiveOptionId(null);
  }, [debouncedQuery, open]);

  useEffect(() => {
    if (!open || activeOptionIndex < 0) return;
    document.getElementById(`admin-model-filter-option-${activeOptionIndex}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeOptionIndex, open]);

  const handleOpenChange = (nextOpen: boolean) => {
    if (!active) return;
    setOpen(nextOpen);
    if (!nextOpen) {
      setQuery('');
      setDebouncedQuery('');
      setActiveOptionId(null);
    }
  };
  const selectModel = (modelId: string) => {
    onChange(modelId);
    setOpen(false);
    setQuery('');
    setDebouncedQuery('');
    setActiveOptionId(null);
  };

  const selectActiveOption = () => {
    if (activeOptionId === null) return;
    if (activeOptionId === '') {
      selectModel('');
      return;
    }
    if (visibleOptionIds.includes(activeOptionId)) selectModel(activeOptionId);
  };

  const moveActiveOption = (direction: 1 | -1) => {
    if (visibleOptionIds.length === 0) return;
    const currentIndex = activeOptionId === null ? -1 : visibleOptionIds.indexOf(activeOptionId);
    const nextIndex = currentIndex < 0
      ? (direction === 1 ? 0 : visibleOptionIds.length - 1)
      : (currentIndex + direction + visibleOptionIds.length) % visibleOptionIds.length;
    setActiveOptionId(visibleOptionIds[nextIndex]);
  };

  return (
    <Popover open={active && open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label={t('admin.filters.model')}
          aria-expanded={active && open}
          aria-controls="admin-model-filter-options"
          className={cn('flex min-w-0 items-center justify-between gap-2 px-3 text-left text-sm', styles.modelTrigger)}
        >
          {selected?.unresolved ? (
            <span className="min-w-0 truncate" title={selected.id}>{selected.id}</span>
          ) : selected ? (
            <span className="flex min-w-0 items-center gap-2" title={`${selected.name} · ${selected.providerLabel} · ${selected.id}`}>
              <span className="shrink-0" aria-hidden="true">
                <ProviderIcon providerId={selected.providerId} size={18} />
              </span>
              <span className="min-w-0 truncate">{selected.name} · {selected.providerLabel} · {selected.id}</span>
            </span>
          ) : (
            <span className="text-muted-foreground">模型不限</span>
          )}
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className={cn('w-[min(28rem,calc(100vw-2rem))] p-0', styles.menu, styles.controls)}>
        <div className="border-b border-border p-2">
          <Input
            type="search"
            role="searchbox"
            aria-label="搜索模型"
            aria-controls="admin-model-filter-options"
            aria-activedescendant={activeOptionIndex >= 0 ? `admin-model-filter-option-${activeOptionIndex}` : undefined}
            value={query}
            onChange={event => setQuery(event.target.value.slice(0, 256))}
            maxLength={256}
            onKeyDown={event => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                moveActiveOption(1);
              } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                moveActiveOption(-1);
              } else if (event.key === 'Enter') {
                event.preventDefault();
                selectActiveOption();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                handleOpenChange(false);
              }
            }}
            placeholder="搜索模型名称或 ID"
            autoFocus
          />
        </div>
        <div
          id="admin-model-filter-options"
          role="listbox"
          aria-label="模型筛选选项"
          className="max-h-72 overflow-y-auto p-1"
        >
          {!debouncedQuery ? (
            <ModelFilterOptionButton
              id="admin-model-filter-option-0"
              active={activeOptionId === ''}
              selected={!value}
              label="模型不限"
              onActive={() => setActiveOptionId('')}
              onSelect={() => selectModel('')}
            />
          ) : null}
          {options.map((option, index) => {
            const renderedIndex = index + allOptionOffset;
            return (
            <ModelFilterOptionButton
              key={option.id}
              id={`admin-model-filter-option-${renderedIndex}`}
              active={activeOptionId === option.id}
              selected={option.id === value}
              label={`${option.name} ${option.providerLabel} ${option.id}`}
              onActive={() => setActiveOptionId(option.id)}
              onSelect={() => selectModel(option.id)}
            >
              <span className="flex min-w-0 flex-1 items-start gap-2">
                {option.providerId ? (
                  <span className="relative mt-0.5 shrink-0" aria-hidden="true">
                    <ProviderIcon providerId={option.providerId} size={20} />
                    {option.historical ? (
                      <History className="absolute -bottom-1 -right-1 h-3 w-3 rounded-full bg-background p-0.5 text-muted-foreground" />
                    ) : null}
                  </span>
                ) : option.historical ? (
                  <History className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                ) : null}
                <span className="min-w-0">
                  <span className="block truncate font-medium">{option.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">{option.providerLabel}</span>
                  <span className="block break-all font-mono text-[11px] text-muted-foreground">{option.id}</span>
                </span>
              </span>
            </ModelFilterOptionButton>
            );
          })}
          {loading ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground" aria-live="polite">正在加载模型…</p>
          ) : error ? (
            <div className="space-y-2 px-3 py-4 text-center text-sm text-destructive">
              <p>{error}</p>
              <Button type="button" variant="outline" size="sm" onClick={() => setRevision(current => current + 1)}>重试</Button>
            </div>
          ) : options.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-muted-foreground">没有匹配的模型</p>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function toModelFilterOption(model: AdminModelSummary | undefined, fallbackId: string): ModelFilterOption {
  if (!model) return { id: fallbackId, name: '已选模型', providerId: '', providerLabel: '目录信息待确认', historical: false, unresolved: true };
  return {
    id: model.model_id,
    name: model.name || model.model_id,
    providerId: model.provider?.trim() || '',
    providerLabel: model.provider_display?.trim() || model.provider?.trim() || '提供商未记录',
    historical: model.catalog_status === 'historical',
    unresolved: false,
  };
}

function mergeKnownModels(current: AdminModelSummary[], incoming: AdminModelSummary[]): AdminModelSummary[] {
  const merged = new Map(current.map(model => [model.model_id, model]));
  incoming.forEach(model => merged.set(model.model_id, model));
  return [...merged.values()];
}

function ModelFilterOptionButton({
  id,
  active,
  selected,
  label,
  onActive,
  onSelect,
  children,
}: {
  id: string;
  active: boolean;
  selected: boolean;
  label: string;
  onActive: () => void;
  onSelect: () => void;
  children?: React.ReactNode;
}) {
  return (
    <button
      id={id}
      type="button"
      role="option"
      aria-label={label}
      aria-selected={selected}
      onMouseEnter={onActive}
      onClick={onSelect}
      className={cn(
        'flex w-full items-start gap-2 rounded-md px-3 py-2 text-left text-sm outline-none hover:bg-accent focus-visible:bg-accent',
        (selected || active) && 'bg-accent/70',
      )}
    >
      {children ?? <span className="min-w-0 flex-1">{label}</span>}
      <Check className={cn('mt-0.5 h-4 w-4 shrink-0', selected ? 'opacity-100' : 'opacity-0')} aria-hidden="true" />
    </button>
  );
}

function AdminConversationDetailView({ conversationId, onBack, onForbidden }: { conversationId: string; onBack: () => void; onForbidden: () => void }) {
  const [messagePage, setMessagePage] = useState(1);
  const [runPage, setRunPage] = useState(1);
  const [toolPage, setToolPage] = useState(1);
  const [filePage, setFilePage] = useState(1);
  const detailLoader = useCallback((signal: AbortSignal) => getAdminConversation(conversationId, signal), [conversationId]);
  const messageLoader = useCallback((signal: AbortSignal) => getAdminConversationMessages(conversationId, { page: messagePage, page_size: 25 }, signal), [conversationId, messagePage]);
  const toolLoader = useCallback((signal: AbortSignal) => getAdminConversationToolCalls(conversationId, { page: toolPage, page_size: 25 }, signal), [conversationId, toolPage]);
  const runLoader = useCallback((signal: AbortSignal) => getAdminConversationAgentRuns(conversationId, { page: runPage, page_size: 25 }, signal), [conversationId, runPage]);
  const fileLoader = useCallback((signal: AbortSignal) => getAdminConversationFiles(conversationId, { page: filePage, page_size: 25 }, signal), [conversationId, filePage]);
  const detail = useAdminAuditResource(detailLoader, onForbidden);
  const messages = useAdminAuditResource(messageLoader, onForbidden);
  const tools = useAdminAuditResource(toolLoader, onForbidden);
  const runs = useAdminAuditResource(runLoader, onForbidden);
  const files = useAdminAuditResource(fileLoader, onForbidden);

  return (
    <section aria-label={`对话详情 ${conversationId}`}>
      <Button variant="ghost" size="sm" className="mb-3" onClick={onBack}><ArrowLeft />返回对话列表</Button>
      {detail.loading ? <AdminLoading /> : detail.error ? <AdminError message={detail.error} onRetry={detail.reload} /> : detail.data ? (
        <div className="mb-4 rounded-xl border border-border bg-card p-4">
          <h1 className="text-lg font-semibold">{detail.data.title || '未命名对话'}</h1>
          <div className="mt-2 grid gap-3 text-xs text-muted-foreground sm:grid-cols-2"><div><span>对话 ID：{detail.data.id}</span><div className="mt-1">模型：{detail.data.model_id || '—'}</div><div>更新时间：{formatAdminDate(detail.data.updated_at)}</div></div><AdminUserIdentity user={detail.data.user} /></div>
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
        <div>
          <h2 className="mb-3 font-semibold">消息</h2>
          {messages.loading ? <AdminLoading /> : messages.error ? <AdminError message={messages.error} onRetry={messages.reload} /> : messages.data?.items.length ? (
            <><div className="space-y-3">{messages.data.items.map(message => <AdminMessageCard key={message.id} message={message} />)}</div><AdminPagination page={messages.data} onPageChange={setMessagePage} /></>
          ) : <AdminEmpty>没有已持久化消息</AdminEmpty>}
        </div>

        <div className="space-y-6">
          <section aria-label="Agent 运行记录"><h2 className="mb-3 font-semibold">Agent 运行</h2>
            {runs.loading ? <AdminLoading /> : runs.error ? <AdminError message={runs.error} onRetry={runs.reload} /> : runs.data?.items.length ? (
              <><AdminExecutionInspector runs={runs.data.items} toolCalls={[]} /><AdminPagination page={runs.data} onPageChange={setRunPage} /></>
            ) : <AdminEmpty>没有 Agent 运行记录</AdminEmpty>}
          </section>

          <section aria-label="工具调用记录"><h2 className="mb-3 font-semibold">工具调用</h2>
            {tools.loading ? <AdminLoading /> : tools.error ? <AdminError message={tools.error} onRetry={tools.reload} /> : tools.data?.items.length ? (
              <><AdminExecutionInspector runs={[]} toolCalls={tools.data.items} /><AdminPagination page={tools.data} onPageChange={setToolPage} /></>
            ) : <AdminEmpty>没有工具调用记录</AdminEmpty>}
          </section>

          <div><h2 className="mb-3 font-semibold">文件元数据</h2>
            {files.loading ? <AdminLoading /> : files.error ? <AdminError message={files.error} onRetry={files.reload} /> : files.data?.items.length ? (
              <><div className="space-y-2">{files.data.items.map(file => <div key={file.id} className="rounded-lg border border-border bg-card p-3 text-sm"><div className="font-medium">{file.original_filename}</div><div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground"><span>{file.mimetype || '未知类型'}</span><span>{formatFileSize(file.size)}</span><span>{file.status || '状态未知'}</span>{file.width && file.height ? <span>{file.width}×{file.height}</span> : null}</div></div>)}</div><AdminPagination page={files.data} onPageChange={setFilePage} /></>
            ) : <AdminEmpty>没有关联文件元数据</AdminEmpty>}
          </div>
        </div>
      </div>
    </section>
  );
}

function parseBoolean(value: '' | 'true' | 'false'): boolean | undefined {
  return value === '' ? undefined : value === 'true';
}

function formatFileSize(value: number | null): string {
  if (value === null) return '大小未知';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
