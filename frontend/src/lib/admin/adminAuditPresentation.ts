import type { AdminJsonValue } from '@/types/adminAudit';

export type AdminAuditActionKind = 'view' | 'modify';

const ACTIONS: Record<string, { label: string; kind: AdminAuditActionKind }> = {
  'admin.audit.users.list': { label: '查询用户列表', kind: 'view' },
  'admin.audit.user.view': { label: '查看用户详情', kind: 'view' },
  'admin.audit.conversations.list': { label: '查询对话列表', kind: 'view' },
  'admin.audit.conversation.view': { label: '查看对话详情', kind: 'view' },
  'admin.audit.messages.list': { label: '查看对话消息', kind: 'view' },
  'admin.audit.tool_calls.list': { label: '查看工具调用', kind: 'view' },
  'admin.audit.agent_runs.list': { label: '查看 Agent 运行', kind: 'view' },
  'admin.audit.files.list': { label: '查看关联文件', kind: 'view' },
  'admin.audit.events.list': { label: '查询操作审计', kind: 'view' },
  'admin.audit.performance_run.import': { label: '导入压测记录', kind: 'modify' },
  'admin.audit.performance_runs.list': { label: '查询压测记录', kind: 'view' },
  'admin.audit.performance_run.view': { label: '查看压测详情', kind: 'view' },
  'admin.audit.models.list': { label: '查询模型列表', kind: 'view' },
  'admin.audit.model.view': { label: '查看模型详情', kind: 'view' },
  'admin.audit.itinerary_stability.view': { label: '查看行程稳定性', kind: 'view' },
  'admin.audit.trajectory.view': { label: '查看运行轨迹', kind: 'view' },
  'admin.audit.trajectory.node_detail.view': { label: '查看轨迹节点详情', kind: 'view' },
  model_visibility_changed: { label: '修改模型可见性', kind: 'modify' },
  'admin.mcp.server.create': { label: '新建 MCP 服务', kind: 'modify' },
  'admin.mcp.server.update': { label: '修改 MCP 服务配置', kind: 'modify' },
  'admin.mcp.server.status': { label: '启用或停用 MCP 服务', kind: 'modify' },
  'admin.mcp.server.test': { label: '测试 MCP 服务连接', kind: 'modify' },
  'admin.mcp.server.tools_refresh': { label: '刷新 MCP 工具列表', kind: 'modify' },
};

const RESOURCE_LABELS: Record<string, string> = {
  user: '用户',
  conversation: '对话',
  conversation_messages: '对话消息',
  conversation_tool_calls: '工具调用',
  conversation_agent_runs: 'Agent 运行',
  conversation_files: '关联文件',
  admin_audit_event: '操作审计',
  performance_run: '压测记录',
  model: '模型',
  itinerary_stability: '行程稳定性',
  conversation_run_trajectory: '运行轨迹',
  conversation_run_tool_node_detail: '轨迹工具节点',
  conversation_run_llm_node_detail: '轨迹模型节点',
  conversation_run_system_prompt_node_detail: '轨迹系统提示词节点',
  model_catalog_control: '模型可见性',
  mcp_server: 'MCP 服务',
};

interface AdminAuditAdminPresentation {
  primary: string;
}

export function adminAuditActionLabel(action: string): string {
  return ACTIONS[action]?.label ?? '其他管理操作';
}

/** 未登记的动作返回 null，界面不猜测它是查看还是修改。 */
export function adminAuditActionKind(action: string): AdminAuditActionKind | null {
  return ACTIONS[action]?.kind ?? null;
}

export function adminAuditResourceLabel(resourceType: string): string {
  return RESOURCE_LABELS[resourceType] ?? '其他访问对象';
}

export function formatAdminAuditAdmin(snapshot: AdminJsonValue): AdminAuditAdminPresentation {
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
    return { primary: '管理员信息未记录' };
  }
  const username = typeof snapshot.username === 'string' ? snapshot.username.trim().slice(0, 80) : '';
  if (!username) return { primary: '管理员信息未记录' };
  return { primary: `@${username}` };
}

interface AdminAuditTargetUserPresentation {
  primary: string;
  detail: string;
}

export function formatAdminAuditTargetUser(
  targetUser: AdminJsonValue | undefined,
  targetUserId: string | null,
): AdminAuditTargetUserPresentation | null {
  if (targetUser && typeof targetUser === 'object') {
    const nickname = typeof targetUser.nickname === 'string' ? targetUser.nickname.trim().slice(0, 80) : '';
    const username = typeof targetUser.username === 'string' ? targetUser.username.trim().slice(0, 80) : '';
    const identity = [nickname, username ? `@${username}` : ''].filter(Boolean).join(' ') || '身份信息不完整';
    return {
      primary: `当前用户：${identity}`,
      detail: identity,
    };
  }
  if (!targetUserId) return null;
  if (targetUser === null) {
    return {
      primary: `用户记录已不存在（${shortAdminAuditId(targetUserId)}）`,
      detail: '用户记录已不存在',
    };
  }
  return {
    primary: `目标用户：${shortAdminAuditId(targetUserId)}（旧记录）`,
    detail: '旧审计记录未包含身份',
  };
}

function shortAdminAuditId(value: string): string {
  const normalized = value.trim();
  return normalized.length <= 8 ? normalized : `…${normalized.slice(-8)}`;
}

const METADATA_LABELS: Record<string, string> = {
  page: '页码',
  page_size: '每页数量',
  environment: '环境',
  schema_version: 'Schema 版本',
  status: '状态',
  has_tools: '工具筛选',
  has_files: '文件筛选',
  created_from: '开始日期',
  created_to: '结束日期',
  model_id: '模型',
  source: '来源',
  q: '查询条件',
  query: '查询条件',
  name: '名称',
  provider: '提供商',
  changed_fields: '修改字段',
  connection_reset: '连接已重置',
  is_enabled: '启用',
  selectable: '可选择',
  revision: '版本',
  health_status: '检测结果',
  error_code: '错误码',
  discovered_tool_count: '发现工具数',
  allowed_tool_count: '授权工具数',
  removed_allowed_tools: '移除的授权工具',
  result_saved: '结果已保存',
};

interface AdminAuditMetadataItem {
  label: string;
  value: string;
}

export function formatAdminAuditMetadata(metadata: AdminJsonValue): AdminAuditMetadataItem[] {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return [];
  return Object.entries(METADATA_LABELS).flatMap(([key, label]) => {
    const value = metadata[key];
    if (value === null || value === undefined) return [];
    if ((key === 'q' || key === 'query') && typeof value === 'object' && !Array.isArray(value)) {
      const present = value?.present === true;
      const length = typeof value?.length === 'number' ? Math.max(0, Math.trunc(value.length)) : null;
      return [{ label, value: present ? `已填写${length === null ? '' : `（${length} 字符）`}` : '未填写' }];
    }
    if (typeof value === 'string') return [{ label, value: value.slice(0, 80) }];
    if (typeof value === 'number') return [{ label, value: String(value) }];
    if (typeof value === 'boolean') return [{ label, value: value ? '是' : '否' }];
    if (Array.isArray(value)) {
      const items = value.filter((item): item is string => typeof item === 'string').slice(0, 20);
      return [{ label, value: items.length > 0 ? items.join('、').slice(0, 200) : '无' }];
    }
    return [];
  });
}
