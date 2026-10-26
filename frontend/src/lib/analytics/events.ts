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

  // —— 对话核心 ——
  // message_sent 在请求发出前记，此时还没有 run_id，用浏览器生成的 message_id 与后续
  // response_* 事件对齐；新对话发出时 conversation_id 还是本地草稿 ID，落地后由
  // conversation_created 给出服务端 ID。
  conversation_created: { conversation_id: string; model: string };
  message_sent: {
    message_id: string;
    conversation_id: string;
    model: string;
    reasoning_enabled: boolean;
    attachment_count: number;
    attachment_types: string[];
    knowledge_base_count: number;
    prompt_chars: number;
    is_new_conversation: boolean;
    is_retry: boolean;
    is_edit_resend: boolean;
  };
  /** 用户感知的首个输出（思考或正文）到达，ttft_ms 从点击发送起算。 */
  response_first_token: {
    message_id: string;
    run_id: string | null;
    conversation_id: string;
    model: string;
    ttft_ms: number;
    output_kind: 'reasoning' | 'content';
  };
  response_completed: {
    message_id: string;
    run_id: string | null;
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
  };
  response_failed: {
    message_id: string;
    run_id: string | null;
    conversation_id: string;
    model: string;
    /** connect=请求没被接受；stream=流中途断且重连用尽；run=服务端 run 报失败。 */
    stage: 'connect' | 'stream' | 'run';
    error_code: string;
    duration_ms: number;
    had_output: boolean;
  };
  response_stopped: {
    message_id: string;
    run_id: string | null;
    conversation_id: string;
    model: string;
    duration_ms: number;
    had_output: boolean;
  };
  /** 流断线后续传成功，attempt 是本次回答里第几次续传。 */
  stream_reconnected: { message_id: string; run_id: string | null; conversation_id: string; attempt: number };
  /** 模型阶段超过阈值没有新输出，界面提示「模型响应变慢」。 */
  stream_slow_hint_shown: { run_id: string | null };
  agent_context_submitted: { run_id: string; conversation_id: string; status: string };
  model_switched: { from_model: string | null; to_model: string; location: 'composer' | 'settings' };
  reasoning_toggled: { enabled: boolean; model: string };
  attachment_added: { type: 'image' | 'file'; size_kb: number; source: 'button' | 'paste' | 'drop' };
  attachment_failed: { type: 'image' | 'file'; stage: 'upload' | 'processing'; error_code: string };
  knowledge_base_toggled: { attached: boolean; knowledge_base_count: number };
  /** composer=输入框显示额度已用完的提示；send=用户仍尝试发送被拦下。 */
  credits_exhausted_shown: { scope: 'daily' | 'weekly'; trigger: 'composer' | 'send' };

  // —— 回答交互 ——
  message_copied: { scope: 'full' | 'code_block'; role: 'assistant' | 'user' | null; run_id: string | null };
  message_edited: { conversation_id: string };
  /** card=点回答下方的来源卡；inline=点正文里的引用角标。 */
  citation_opened: { run_id: string | null; source: 'card' | 'inline' };
  evidence_sidebar_opened: { run_id: string | null };
  trajectory_opened: { run_id: string; conversation_id: string };
  agent_process_toggled: { run_id: string; expanded: boolean };
  generated_image_opened: { run_id: string | null };
  generated_image_regenerate_clicked: { run_id: string | null };
  document_opened: { document_id: string; version: number };

  // —— 会话管理 ——
  conversation_opened: { conversation_id: string; source: 'sidebar' | 'search' };
  conversation_searched: { query_chars: number; result_count: number };
  conversation_renamed: { conversation_id: string };
  conversation_deleted: { conversation_id: string };

  // —— 设置、记忆与知识库 ——
  settings_opened: { panel: string };
  knowledge_base_created: Record<string, never>;
  knowledge_base_deleted: Record<string, never>;
  knowledge_file_uploaded: { size_kb: number; file_type: string };
  knowledge_file_failed: { stage: 'validation' | 'upload'; error_code: string };
  memory_viewed: { enabled: boolean; memory_count: number };
  memory_deleted: { scope: 'single' | 'all' };
  changelog_opened: { changelog_id: string | null };
  notification_clicked: { kind: string; target_type: string };
}

export type AnalyticsEventName = keyof AnalyticsEvents;
