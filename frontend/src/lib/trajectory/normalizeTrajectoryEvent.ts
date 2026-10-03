import type {
  ContextToolVisibility,
  LlmOutputProvenance,
  TrajectoryCapabilityResolution,
} from '@/types/trajectory';

export interface NormalizedTrajectoryEvent {
  runId: string;
  sequence: number;
  eventType: string;
  /** 0 表示 P0 升级前缺失的 legacy schema_version。 */
  schemaVersion: number;
  timestamp: string;
  stepId: string | null;
  toolCallId: string | null;
  parentStepId: string | null;
  traceId: string | null;
  payload: Record<string, unknown>;
}

const SUPPORTED_SCHEMA_VERSIONS = new Set([0, 1]);
const MAX_LEDGER_TEXT_LENGTH = 512;
const MAX_LEDGER_LIST_ITEMS = 50;
const SECRET_PATTERN = /\b(api[_-]?key|authorization|access[_-]?token|token|password|secret)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+/gi;
const ISO_TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-](\d{2}):(\d{2}))$/;

const EVENT_PAYLOAD_FIELDS: Record<string, readonly string[]> = {
  run_started: ['conversation_id', 'message_id', 'task_id', 'model', 'tools', 'capability_resolution'],
  step_started: ['step_number'],
  tool_call_started: ['tool_name', 'plan_item_id'],
  tool_call_delta: ['tool_name'],
  tool_call_completed: ['tool_name', 'status', 'duration_ms', 'plan_item_id'],
  step_completed: ['step_number', 'tool_call_count', 'duration_ms'],
  run_limit_reached: ['reason'],
  run_interrupted: ['reason'],
  run_failed: ['error_code', 'message'],
  run_completed: ['total_steps', 'total_tool_calls', 'finish_reason'],
  llm_round_started: [
    'llm_round_id', 'round_index', 'model', 'provider', 'system_prompt_fingerprint',
    'context_visibility', 'tool_names',
  ],
  llm_round_first_output_delta: ['llm_round_id', 'delta_kind', 'ttft_ms'],
  llm_round_completed: [
    'llm_round_id', 'status', 'finish_reason', 'input_tokens', 'output_tokens', 'total_tokens',
    'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens', 'ttft_ms', 'duration_ms', 'output_provenance',
  ],
  llm_round_failed: ['llm_round_id', 'status', 'error_code', 'message', 'output_provenance'],
  llm_round_cancelled: ['llm_round_id', 'status', 'reason', 'output_provenance'],
  retrieval_started: ['retrieval_id', 'query_summary'],
  retrieval_completed: ['retrieval_id', 'status', 'document_count', 'duration_ms'],
  retrieval_failed: ['retrieval_id', 'status', 'error_code', 'message'],
  retrieval_cancelled: ['retrieval_id', 'status', 'reason'],
  tool_attempt_started: ['tool_attempt_id', 'tool_name', 'attempt_index'],
  tool_attempt_completed: ['tool_attempt_id', 'status', 'error_code', 'duration_ms'],
  suggested_questions_pending: ['protocol_version', 'message_id', 'revision', 'status'],
  suggested_questions_ready: ['protocol_version', 'message_id', 'revision', 'status', 'duration_ms'],
  conversation_title_updated: ['protocol_version', 'conversation_id', 'duration_ms'],
  run_progress_updated: [
    'protocol_version', 'phase', 'label', 'completed_steps', 'total_steps',
    'completed_tool_calls', 'max_tool_calls',
  ],
  plan_snapshot: ['protocol_version', 'plan_id', 'mode', 'source', 'revision', 'reason', 'items'],
  plan_step_updated: ['protocol_version', 'plan_id', 'mode', 'source', 'revision', 'reason', 'item'],
  tool_result_digest: [
    'protocol_version', 'tool_name', 'status', 'title', 'summary', 'key_findings', 'source_refs',
    'truncated', 'repair_state', 'repair_id', 'plan_item_id',
  ],
  evidence_item_upserted: ['protocol_version', 'evidence'],
  content_block_upserted: ['protocol_version'],
  content_block_discarded: ['protocol_version', 'block_id'],
  system_prompt_prepared: [
    'protocol_version', 'status', 'source', 'template_version', 'section_ids',
    'fingerprint', 'char_count', 'duration_ms', 'error_code', 'message', 'detail_status',
  ],
  capability_escalated: [
    'protocol_version', 'step_number', 'from_package_id', 'capability_resolution', 'section_ids',
    'system_prompt_fingerprint',
  ],
  context_status_updated: [
    'protocol_version', 'message_id', 'phase', 'status', 'round_index', 'window_tokens',
    'estimated_tokens_before', 'estimated_tokens_after', 'actual_prompt_tokens', 'removed_turns',
    'removed_messages', 'removed_tool_transactions',
  ],
  context_required: ['protocol_version', 'context_type', 'request_id', 'purpose', 'reason', 'expires_at'],
  context_result: ['protocol_version', 'context_type', 'request_id', 'status'],
};

const PLAN_ITEM_FIELDS = new Set([
  'id', 'title', 'phase_id', 'phase_title', 'status', 'kind', 'summary', 'tool_names',
  'evidence_item_ids', 'depends_on', 'planned_tools',
]);
const PLAN_ITEM_LIST_FIELDS = new Set([
  'tool_names', 'evidence_item_ids', 'depends_on', 'planned_tools',
]);
const EVIDENCE_FIELDS = new Set([
  'id', 'kind', 'status', 'title', 'url', 'domain', 'claim', 'snippet',
  'used_by_final_answer', 'citation_index',
]);
const LIST_FIELDS = new Set(['tools', 'key_findings', 'source_refs', 'section_ids']);
// 后端契约里的 package_id 与 reason code 都是服务端生成的短标识符；UI 只校验形状。
const CAPABILITY_IDENTIFIER_PATTERN = /^[a-z][a-z0-9_]{0,47}$/;
const CAPABILITY_RESOLUTION_COMMON_FIELDS = [
  'schema_version',
  'router_version',
  'package_id',
  'confidence',
  'resolution_mode',
  'reason_codes',
  'external_tool_names',
  'effective_plan_mode',
  'include_current_date',
  'network_boundary_required',
] as const;
// v1 与 v2 现在字段相同；版本号只标识路由协议代次。
const CAPABILITY_RESOLUTION_FIELDS = new Set([
  ...CAPABILITY_RESOLUTION_COMMON_FIELDS,
  'bundle_fingerprint',
]);
const CAPABILITY_CONFIDENCE = new Set(['high', 'medium', 'low']);
const CAPABILITY_RESOLUTION_MODES = new Set(['routed', 'degraded', 'clarification']);
const CAPABILITY_PLAN_MODES = new Set(['auto', 'on', 'off']);
const ROUTER_VERSION_PATTERN = /^\d{4}-\d{2}-\d{2}\.\d+$/;
const BUNDLE_FINGERPRINT_PATTERN = /^sha256:[0-9a-f]{64}$/;
const TOOL_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_-]{0,127}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isUniqueIdentifierList(
  value: unknown,
  minLength: number,
  maxLength: number,
): value is string[] {
  return Array.isArray(value)
    && value.length >= minLength
    && value.length <= maxLength
    && value.every(item => typeof item === 'string' && CAPABILITY_IDENTIFIER_PATTERN.test(item))
    && new Set(value).size === value.length;
}

/** 将实时与历史来源统一收敛为同一个有界能力路由对象。 */
export function normalizeTrajectoryCapabilityResolution(
  value: unknown,
): TrajectoryCapabilityResolution | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value);
  if ((value.schema_version !== 1 && value.schema_version !== 2)
    || keys.length !== CAPABILITY_RESOLUTION_FIELDS.size
    || keys.some(key => !CAPABILITY_RESOLUTION_FIELDS.has(key))) return null;
  if (typeof value.router_version !== 'string'
    || value.router_version.length > 32
    || !ROUTER_VERSION_PATTERN.test(value.router_version)
    || typeof value.package_id !== 'string'
    || !CAPABILITY_IDENTIFIER_PATTERN.test(value.package_id)
    || typeof value.confidence !== 'string'
    || !CAPABILITY_CONFIDENCE.has(value.confidence)
    || typeof value.resolution_mode !== 'string'
    || !CAPABILITY_RESOLUTION_MODES.has(value.resolution_mode)
    || !isUniqueIdentifierList(value.reason_codes, 1, 4)
    || !Array.isArray(value.external_tool_names)
    || value.external_tool_names.length > 5
    || value.external_tool_names.some(tool => (
      typeof tool !== 'string' || !TOOL_NAME_PATTERN.test(tool) || tool === 'update_plan'
    ))
    || new Set(value.external_tool_names).size !== value.external_tool_names.length
    || typeof value.effective_plan_mode !== 'string'
    || !CAPABILITY_PLAN_MODES.has(value.effective_plan_mode)
    || typeof value.include_current_date !== 'boolean'
    || typeof value.network_boundary_required !== 'boolean'
    || typeof value.bundle_fingerprint !== 'string'
    || !BUNDLE_FINGERPRINT_PATTERN.test(value.bundle_fingerprint)) return null;

  const common = {
    router_version: value.router_version,
    package_id: value.package_id,
    confidence: value.confidence as TrajectoryCapabilityResolution['confidence'],
    resolution_mode: value.resolution_mode as TrajectoryCapabilityResolution['resolution_mode'],
    reason_codes: [...value.reason_codes],
    external_tool_names: [...value.external_tool_names],
    effective_plan_mode: value.effective_plan_mode as TrajectoryCapabilityResolution['effective_plan_mode'],
    include_current_date: value.include_current_date,
    network_boundary_required: value.network_boundary_required,
    bundle_fingerprint: value.bundle_fingerprint,
  };
  const resolution: TrajectoryCapabilityResolution = value.schema_version === 1
    ? { schema_version: 1, ...common }
    : { schema_version: 2, ...common };
  // UI 只做结构性校验与降级展示：能力包与工具、计划模式、日期、reason code 的语义
  // 一致性由后端 run_capability_contract 保证，前端不再维护第二份判定（issue #26）。
  return resolution;
}

function nullableString(value: unknown): string | null | undefined {
  return value === null || typeof value === 'string' ? value : undefined;
}

function boundedText(value: unknown): string {
  return String(value).replace(SECRET_PATTERN, (_, key: string) => `${key}=[REDACTED]`)
    .slice(0, MAX_LEDGER_TEXT_LENGTH);
}

function boundedList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_LEDGER_LIST_ITEMS).map(boundedText);
}

function safeUrl(value: unknown): string | null {
  if (value === null) return null;
  try {
    const url = new URL(boundedText(value));
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return null;
    return `${url.protocol}//${url.host}${url.pathname}`.slice(0, MAX_LEDGER_TEXT_LENGTH);
  } catch {
    return null;
  }
}

function sanitizeScalar(value: unknown): unknown {
  return value === null || typeof value === 'boolean' || typeof value === 'number'
    ? value
    : boundedText(value);
}

function sanitizePlanItem(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  const item: Record<string, unknown> = {};
  for (const field of PLAN_ITEM_FIELDS) {
    if (!(field in value)) continue;
    item[field] = PLAN_ITEM_LIST_FIELDS.has(field)
      ? boundedList(value[field])
      : sanitizeScalar(value[field]);
  }
  return item;
}

function sanitizePlanItems(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, MAX_LEDGER_LIST_ITEMS).map(sanitizePlanItem);
}

function sanitizeEvidence(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  const evidence: Record<string, unknown> = {};
  for (const field of EVIDENCE_FIELDS) {
    if (!(field in value)) continue;
    evidence[field] = field === 'url' ? safeUrl(value[field]) : sanitizeScalar(value[field]);
  }
  return evidence;
}

function normalizeSchemaVersion(value: unknown): number | null {
  if (value === undefined || value === null) return 0;
  return typeof value === 'number' && Number.isInteger(value) && SUPPORTED_SCHEMA_VERSIONS.has(value)
    ? value
    : null;
}

function canonicalTimestamp(value: string): string | null {
  const match = ISO_TIMESTAMP_PATTERN.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[9] === undefined ? 0 : Number(match[9]);
  const offsetMinute = match[10] === undefined ? 0 : Number(match[10]);
  const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, isLeapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    month < 1
    || month > 12
    || day < 1
    || day > daysInMonth[month - 1]
    || hour > 23
    || minute > 59
    || second > 59
    || offsetHour > 23
    || offsetMinute > 59
  ) return null;
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? null : timestamp.toISOString();
}

/** 对实时 SSE 和历史账本使用相同的有界字段白名单。 */
export function normalizeOutputProvenance(value: unknown): LlmOutputProvenance | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  const disposition = (['emitted', 'suppressed', 'replaced'] as const).find(item => item === candidate.disposition);
  const source = (['model', 'server', 'none'] as const).find(item => item === candidate.source);
  const reason = (['streamed', 'deferred', 'server_rewrite', 'product_guard', 'knowledge_guard', 'plan_continues', 'tool_round', 'tool_retracted', 'research_guard', 'summary_guard', 'no_content', 'not_committed', 'round_failed', 'round_cancelled'] as const).find(item => item === candidate.reason);
  const blockId = candidate.block_id ?? null;
  if (!disposition || !source || !reason
    || (blockId !== null && (typeof blockId !== 'string' || blockId.length < 1 || blockId.length > 128))) return null;
  return { disposition, source, reason, block_id: blockId };
}

export function normalizeContextToolVisibility(value: unknown): ContextToolVisibility | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.schema_version !== 1 || row.scope !== 'application_messages_after_context_management'
    || typeof row.context_status !== 'string' || !/^[a-z_]{1,64}$/.test(row.context_status)
    || typeof row.truncated !== 'boolean') return null;
  for (const field of ['before', 'visible', 'removed']) {
    const ids = row[`${field}_tool_call_ids`];
    const count = row[`${field}_count`];
    if (!Array.isArray(ids) || ids.length > 200 || !ids.every(id => typeof id === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(id))
      || typeof count !== 'number' || !Number.isInteger(count) || count < ids.length) return null;
  }
  return {
    schema_version: 1, scope: row.scope, context_status: row.context_status, truncated: row.truncated,
    before_tool_call_ids: row.before_tool_call_ids as string[], visible_tool_call_ids: row.visible_tool_call_ids as string[], removed_tool_call_ids: row.removed_tool_call_ids as string[],
    before_count: row.before_count as number, visible_count: row.visible_count as number, removed_count: row.removed_count as number,
  };
}

function sanitizePayload(eventType: string, source: Record<string, unknown>): Record<string, unknown> | null {
  const fields = EVENT_PAYLOAD_FIELDS[eventType];
  if (!fields) return null;

  if (eventType === 'system_prompt_prepared' && (
    source.protocol_version !== 2 || (source.status !== 'ready' && source.status !== 'failed')
    || source.source !== 'code' || typeof source.template_version !== 'string'
    || !Array.isArray(source.section_ids) || !source.section_ids.every(item => typeof item === 'string')
    || typeof source.duration_ms !== 'number' || !Number.isInteger(source.duration_ms) || source.duration_ms < 0
  )) return null;
  const payload: Record<string, unknown> = {};
  for (const field of fields) {
    if (!(field in source)) continue;
    if (field === 'fingerprint' || field === 'system_prompt_fingerprint') {
      if (source[field] === null || (typeof source[field] === 'string' && /^[a-f0-9]{64}$/i.test(source[field]))) payload[field] = source[field];
    } else if (eventType === 'system_prompt_prepared' && field === 'char_count') {
      if (source[field] === null || (typeof source[field] === 'number' && Number.isInteger(source[field]) && source[field] >= 0)) payload[field] = source[field];
    } else if (eventType === 'system_prompt_prepared' && field === 'detail_status') {
      if (source[field] === null || source[field] === 'available' || source[field] === 'degraded') payload[field] = source[field];
    } else if (eventType === 'system_prompt_prepared' && field === 'message') {
      if (source[field] === null
        || source[field] === '系统提示词组装失败'
        || source[field] === '系统提示词组装失败，请稍后重试。') payload[field] = source[field];
    } else if (field === 'capability_resolution') {
      const resolution = normalizeTrajectoryCapabilityResolution(source[field]);
      if (resolution) payload[field] = resolution;
    } else if (field === 'context_visibility') payload[field] = normalizeContextToolVisibility(source[field]);
    else if (field === 'output_provenance') payload[field] = normalizeOutputProvenance(source[field]);
    else if (field === 'items') payload[field] = sanitizePlanItems(source[field]);
    else if (field === 'item') payload[field] = sanitizePlanItem(source[field]);
    else if (field === 'evidence') payload[field] = sanitizeEvidence(source[field]);
    else if (LIST_FIELDS.has(field)) payload[field] = boundedList(source[field]);
    else payload[field] = sanitizeScalar(source[field]);
  }
  if (eventType === 'run_started' && readDynamicToolDiscoveryEnabled(source)) {
    payload.dynamic_tool_discovery_enabled = true;
  }
  return payload;
}

/** 只保留显式开启标记。授权目录不是已加载或已执行的工具，不能进入轨迹。 */
function readDynamicToolDiscoveryEnabled(source: Record<string, unknown>): boolean {
  if (source.dynamic_tool_discovery_enabled === true) return true;
  const config = source.config;
  if (!config || typeof config !== 'object' || Array.isArray(config)) return false;
  const discovery = (config as Record<string, unknown>).dynamic_tool_discovery;
  if (!discovery || typeof discovery !== 'object' || Array.isArray(discovery)) return false;
  return (discovery as Record<string, unknown>).enabled === true;
}

function hasValidEnvelope(
  source: Record<string, unknown>,
): source is Record<string, unknown> & { type: string; run_id: string; sequence: number; ts: number; trace_id: string } {
  return typeof source.type === 'string'
    && typeof source.run_id === 'string'
    && typeof source.sequence === 'number'
    && Number.isInteger(source.sequence)
    && source.sequence >= 0
    && typeof source.ts === 'number'
    && Number.isFinite(source.ts)
    && typeof source.trace_id === 'string';
}

function hasValidRecordEnvelope(
  source: Record<string, unknown>,
): source is Record<string, unknown> & {
  sequence: number;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
} {
  return typeof source.sequence === 'number'
    && Number.isInteger(source.sequence)
    && source.sequence >= 0
    && typeof source.event_type === 'string'
    && typeof source.timestamp === 'string'
    && isRecord(source.payload);
}

/** 将实时 agent_event 转成普通用户轨迹可消费的受控事件。 */
export function normalizeSseTrajectoryEvent(input: unknown): NormalizedTrajectoryEvent | null {
  if (!isRecord(input) || !hasValidEnvelope(input)) {
    return null;
  }
  const schemaVersion = normalizeSchemaVersion(input.schema_version);
  if (schemaVersion === null) return null;
  const stepId = nullableString(input.step_id);
  const toolCallId = nullableString(input.tool_call_id);
  const parentStepId = nullableString(input.parent_step_id);
  if (stepId === undefined || toolCallId === undefined || parentStepId === undefined) return null;

  const payload = sanitizePayload(input.type, input);
  if (payload === null) return null;
  const timestamp = new Date(input.ts * 1000);
  if (Number.isNaN(timestamp.getTime())) return null;

  return {
    runId: input.run_id,
    sequence: input.sequence,
    eventType: input.type,
    schemaVersion,
    timestamp: timestamp.toISOString(),
    stepId,
    toolCallId,
    parentStepId,
    traceId: input.trace_id,
    payload,
  };
}

/** 将 P1 durable record 转成与实时 SSE 相同的普通用户事件。 */
export function normalizeTrajectoryRecord(runId: string, input: unknown): NormalizedTrajectoryEvent | null {
  if (!isRecord(input)
    || typeof runId !== 'string'
    || !hasValidRecordEnvelope(input)) {
    return null;
  }
  const record = input;
  const schemaVersion = normalizeSchemaVersion(record.schema_version);
  if (schemaVersion === null) return null;
  const stepId = nullableString(record.step_id);
  const toolCallId = nullableString(record.tool_call_id);
  const parentStepId = nullableString(record.parent_step_id);
  const traceId = nullableString(record.trace_id);
  if (stepId === undefined || toolCallId === undefined || parentStepId === undefined || traceId === undefined) {
    return null;
  }
  if (record.payload.type !== undefined && record.payload.type !== record.event_type) return null;
  if (record.payload.run_id !== undefined && record.payload.run_id !== runId) return null;

  const payload = sanitizePayload(record.event_type, record.payload);
  if (payload === null) return null;
  const timestamp = canonicalTimestamp(record.timestamp);
  if (timestamp === null) return null;

  return {
    runId,
    sequence: record.sequence,
    eventType: record.event_type,
    schemaVersion,
    timestamp,
    stepId,
    toolCallId,
    parentStepId,
    traceId,
    payload,
  };
}
