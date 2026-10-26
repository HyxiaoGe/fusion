/**
 * 产品埋点事件表：浏览器侧所有自定义事件的唯一来源。
 *
 * 约定（2026-10-26 定）：
 * - 事件名用「对象_动作」的 snake_case，一个事件只表示一件已发生的事。上线后的事件名不改，
 *   改名等于新事件、历史漏斗会断；属性可以随时补。
 * - 不送内容：消息文本、回答、文件名、知识库名、搜索词一律不送，只送长度、数量、类型和 ID。
 * - 和一次回答有关的事件都带 run_id 与 conversation_id，和服务端日志、LiteLLM 消费记录按 run_id 对齐。
 * - 页面浏览、点击、JS 异常、Web Vitals、会话回放由 PostHog 自带采集，不在这里定义。
 * - 公共属性（构建版本、是否游客、语言）由 analytics.ts 统一注册，这里只列事件独有的属性。
 * - 浏览器看不到的步骤（注册落库、run 结算、备用切换、额度、知识库入库）在后端采，不在此表。
 *
 * 新增事件：先在这里加类型，再在触发点调用 track()；业务代码不直接调 PostHog。
 */

export type LoginMethod = 'sso' | 'email_code';
export type SsoProviderName = 'github' | 'google';
/** 登录弹窗是从哪里打开的。 */
export type LoginDialogSource = 'initial' | 'header' | 'login_gate';
export type LoginGateReason = 'guest_limit' | 'feature' | 'guest_invalid' | 'guest_unavailable';

export interface AnalyticsEvents {
  // —— 获客与登录 ——
  home_showcase_clicked: { card_id: string; action: 'view' | 'reuse' };
  suggested_question_clicked: { position: 'home' | 'followup'; index: number };
  guest_session_started: Record<string, never>;
  login_gate_shown: { reason: LoginGateReason };
  login_started: { method: LoginMethod; provider?: SsoProviderName; source: LoginDialogSource };
  login_code_sent: { is_resend: boolean };
  login_completed: { method: LoginMethod; provider?: SsoProviderName | null };
  login_failed: {
    method: LoginMethod;
    provider?: SsoProviderName;
    stage: 'start' | 'send_code' | 'verify' | 'callback';
    error_code: string;
  };
  logout: Record<string, never>;

  // —— 对话核心（第二批接入）——
  conversation_created: {
    conversation_id: string;
    source: 'new_button' | 'home' | 'showcase_reuse' | 'suggested_question';
  };
  message_sent: {
    run_id: string;
    conversation_id: string;
    model: string;
    reasoning_enabled: boolean;
    attachment_count: number;
    attachment_types: string[];
    knowledge_base_count: number;
    prompt_chars: number;
    is_first_message: boolean;
    is_edit_resend: boolean;
  };
  response_first_token: { run_id: string; conversation_id: string; model: string; ttft_ms: number };
  response_completed: {
    run_id: string;
    conversation_id: string;
    model: string;
    duration_ms: number;
    rounds: number;
    tool_call_count: number;
    tools: string[];
    fallback_model: string | null;
    finish_reason: string;
    output_chars: number;
    image_count: number;
    document_count: number;
    credits_cost: number | null;
  };
  response_failed: {
    run_id: string;
    conversation_id: string;
    model: string;
    stage: 'connect' | 'stream' | 'parse';
    error_code: string;
    duration_ms: number;
  };
  response_stopped: { run_id: string; conversation_id: string; elapsed_ms: number; had_output: boolean };
  stream_slow_hint_shown: { run_id: string; conversation_id: string; model: string; gap_ms: number };
  stream_reconnected: { run_id: string; conversation_id: string; attempt: number; reason: string };
  agent_context_submitted: { run_id: string; conversation_id: string; kind: string };
  model_switched: { from_model: string; to_model: string; location: 'composer' | 'settings' };
  reasoning_toggled: { enabled: boolean; model: string };
  attachment_added: { type: 'image' | 'file'; size_kb: number; source: 'button' | 'paste' | 'drop' };
  attachment_failed: { type: 'image' | 'file'; error_code: string };
  knowledge_base_toggled: { attached: boolean; knowledge_base_count: number };
  credits_exhausted_shown: { scope: 'daily' | 'weekly' };

  // —— 回答交互与会话管理（第二批接入）——
  message_copied: { run_id: string; scope: 'full' | 'code_block' };
  message_edited: { conversation_id: string };
  citation_opened: { run_id: string; kind: 'web' | 'knowledge' | 'video' };
  evidence_sidebar_opened: { run_id: string };
  trajectory_opened: { run_id: string };
  tool_result_expanded: { run_id: string; tool: string };
  generated_image_opened: { run_id: string };
  image_edit_requested: { run_id: string };
  video_played: { run_id: string; platform: string };
  document_opened: { document_id: string; version: number };
  conversation_opened: { conversation_id: string; source: 'sidebar' | 'search' | 'url' };
  conversation_searched: { query_chars: number; result_count: number };
  conversation_renamed: { conversation_id: string };
  conversation_deleted: { conversation_id: string };

  // —— 设置与知识库（第二批接入）——
  settings_opened: { panel: string };
  knowledge_base_created: Record<string, never>;
  knowledge_base_deleted: Record<string, never>;
  knowledge_file_uploaded: { size_kb: number; file_type: string };
  knowledge_file_failed: { error_code: string };
  memory_viewed: Record<string, never>;
  memory_deleted: Record<string, never>;
  changelog_opened: { version: string };
  notification_clicked: { kind: string };
}

export type AnalyticsEventName = keyof AnalyticsEvents;
