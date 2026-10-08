/** Run 的工具模式：v3 起不再按问题分类，只区分有无工具与是否深度研究。 */
export type TrajectoryCapabilityModeId =
  | 'agent'
  | 'deep_research'
  | 'knowledge_grounded'
  | 'tools_unavailable';

/** 后端新增模式时 UI 原样展示 id，不丢弃整条 resolution。 */
export type TrajectoryCapabilityModeIdOrUnknown = TrajectoryCapabilityModeId | (string & {});

export type TrajectoryCapabilityReasonCode =
  | 'all_available_tools'
  | 'deep_research_mode'
  | 'knowledge_grounded_mode'
  | 'tools_disabled'
  | 'function_calling_unavailable'
  | 'search_capability_unavailable'
  | 'required_tools_unavailable';

export type TrajectoryCapabilityReasonCodeOrUnknown = TrajectoryCapabilityReasonCode | (string & {});

/** Run 级工具边界的受控 wire DTO（v3）；更早版本的记录读侧视为未记录。 */
export interface TrajectoryCapabilityResolution {
  schema_version: 3;
  router_version: string;
  package_id: TrajectoryCapabilityModeIdOrUnknown;
  reason_codes: TrajectoryCapabilityReasonCodeOrUnknown[];
  external_tool_names: string[];
  deferred_tool_names: string[];
  effective_plan_mode: 'on' | 'off';
  network_boundary_required: boolean;
  bundle_fingerprint: string;
}

/** P1 普通用户轨迹读取端点的 wire DTO；字段保持后端 snake_case。 */
export interface TrajectoryRunSummary {
  run_id: string;
  message_id: string | null;
  turn_message_id: string | null;
  attempt_index: number | null;
  status: string;
  trajectory_status: string;
  total_steps: number;
  total_tool_calls: number;
  duration_ms: number | null;
  started_at: string;
  ended_at: string | null;
  llm_detail_schema_version: number | null;
  llm_round_count: number;
  /** 旧 API/缓存可能缺失；新历史 Run 会显式返回 null。 */
  capability_resolution?: TrajectoryCapabilityResolution | null;
  /**
   * 持久化 run config 里的动态发现开关。只表示该运行启用了发现，
   * 不表示目录中的工具已经加载或执行。
   */
  dynamic_tool_discovery_enabled?: boolean | null;
}

export interface TrajectoryRunListResponse {
  items: TrajectoryRunSummary[];
  truncated: boolean;
}

export interface TrajectoryRecord {
  sequence: number;
  event_type: string;
  schema_version: number;
  timestamp: string;
  step_id: string | null;
  tool_call_id: string | null;
  parent_step_id: string | null;
  trace_id: string | null;
  span_id: string | null;
  payload: Record<string, unknown>;
}

export interface TrajectorySpan {
  span_id: string;
  kind: string;
  name: string;
  parent_span_id: string | null;
  start_sequence: number;
  end_sequence: number | null;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  status: string;
  terminal_source: string | null;
  inferred_reason: string | null;
  ttft_ms: number | null;
  record_sequences: number[];
}

export interface TrajectoryCompleteness {
  status: string;
  degraded_reason: string | null;
  event_count: number | null;
  expected_last_sequence: number | null;
  loaded_event_count: number;
  first_sequence: number | null;
  last_sequence: number | null;
}

export interface TrajectorySnapshot {
  run: TrajectoryRunSummary;
  records: TrajectoryRecord[];
  spans: TrajectorySpan[];
  completeness: TrajectoryCompleteness;
  truncated: boolean;
  llm_round_summaries: TrajectoryLlmRoundSummary[];
}

export interface TrajectoryLlmRoundSummary {
  llm_round_id: string;
  reasoning_preview: string | null;
  output_preview: string | null;
}

/** P3 普通用户 Tool Node Detail 端点的 wire DTO；字段保持后端 snake_case。 */
export type TrajectoryNodeDetailStatus = 'available' | 'pending' | 'not_recorded' | 'degraded';

export type TrajectoryNodeDetailSection =
  | 'summary'
  | 'payload'
  | 'result'
  | 'timing'
  | 'schema'
  | 'thinking'
  | 'output'
  | 'prompt';

export type TrajectoryToolNodeDetailSection = Extract<
  TrajectoryNodeDetailSection,
  'summary' | 'payload' | 'result' | 'timing' | 'schema'
>;

export interface ToolObservation {
  schema_version: 1;
  status: 'available' | 'not_recorded' | 'capture_failed';
  text?: string | null;
  original_chars?: number | null;
  generated_round_index?: number | null;
  llm_round_id?: string | null;
  redacted_fields: string[];
  truncated_fields: string[];
}

export interface ContextToolVisibility {
  schema_version: 1;
  scope: 'application_messages_after_context_management';
  context_status: string;
  before_tool_call_ids: string[];
  visible_tool_call_ids: string[];
  removed_tool_call_ids: string[];
  before_count: number;
  visible_count: number;
  removed_count: number;
  truncated: boolean;
}

export interface TrajectoryToolNodeDetail {
  tool_call_id: string;
  tool_name: string;
  status: string;
  duration_ms: number | null;
  payload: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  error: Record<string, string> | null;
  observation?: ToolObservation;
}

export interface LlmOutputProvenance {
  disposition: 'emitted' | 'suppressed' | 'replaced';
  source: 'model' | 'server' | 'none';
  reason: 'streamed' | 'deferred' | 'server_rewrite' | 'product_guard' | 'knowledge_guard' | 'plan_continues' | 'tool_round' | 'tool_retracted' | 'content_filtered' | 'research_guard' | 'summary_guard' | 'no_content' | 'not_committed' | 'round_failed' | 'round_cancelled';
  block_id: string | null;
}

export interface TrajectoryLlmNodeDetail {
  llm_round_id: string;
  reasoning_text: string | null;
  output_text: string | null;
  output_provenance?: LlmOutputProvenance | null;
  context_visibility?: ContextToolVisibility | null;
}

export interface TrajectorySystemPromptNodeDetail {
  template_version: string;
  fingerprint: string;
  char_count: number;
  sections: Array<{ section_id: string; content: string }>;
}

export interface TrajectoryNodeDetailResponse {
  status: TrajectoryNodeDetailStatus;
  node_type: 'tool' | 'llm' | 'system_prompt';
  available_sections: TrajectoryNodeDetailSection[];
  detail: TrajectoryToolNodeDetail | TrajectoryLlmNodeDetail | TrajectorySystemPromptNodeDetail | null;
  redacted_fields: string[];
  truncated_fields: string[];
  reason: string | null;
}
