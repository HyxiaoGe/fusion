const MAX_ROUTE_ID_LENGTH = 200;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;

export function normalizeAdminRouteId(value: string | null | undefined): string | undefined {
  if (value == null) return undefined;
  const normalized = value.trim();
  if (!normalized || normalized.length > MAX_ROUTE_ID_LENGTH || CONTROL_CHARACTER_PATTERN.test(normalized)) {
    return undefined;
  }
  return normalized;
}

/** 动态路由段可能仍是编码形态；非法编码按原值处理。 */
export function decodeAdminRouteSegment(value: string | string[] | undefined): string | undefined {
  if (typeof value !== 'string') return undefined;
  let decoded = value;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    decoded = value;
  }
  return normalizeAdminRouteId(decoded);
}

function withQuery(path: string, params: Record<string, string | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const normalized = normalizeAdminRouteId(value);
    if (normalized) query.set(key, normalized);
  }
  const search = query.toString();
  return search ? `${path}?${search}` : path;
}

export const adminPaths = {
  users: '/admin/users',
  user: (userId: string) => `/admin/users/${encodeURIComponent(userId)}`,
  conversations: (filters: { userId?: string; modelId?: string } = {}) => withQuery('/admin/conversations', {
    user_id: filters.userId,
    model_id: filters.modelId,
  }),
  conversation: (conversationId: string) => `/admin/conversations/${encodeURIComponent(conversationId)}`,
  models: '/admin/models',
  model: (modelId: string) => `/admin/models/${encodeURIComponent(modelId)}`,
  auditLog: '/admin/audit-log',
  itineraryQuality: '/admin/quality/itinerary',
  modelVisibility: '/admin/model-visibility',
  mcp: '/admin/mcp',
  usage: '/admin/usage',
} as const;

/** 旧版 /admin?tab=... 链接映射到路径路由，未知参数一律回到用户列表。 */
export function legacyAdminPath(searchParams: URLSearchParams): string {
  const tab = searchParams.get('tab');
  const userId = normalizeAdminRouteId(searchParams.get('user_id'));
  const modelId = normalizeAdminRouteId(searchParams.get('model_id'));
  if (tab === 'conversations') {
    const conversationId = normalizeAdminRouteId(searchParams.get('conversation_id'));
    return conversationId ? adminPaths.conversation(conversationId) : adminPaths.conversations({ userId, modelId });
  }
  if (tab === 'models') return modelId ? adminPaths.model(modelId) : adminPaths.models;
  if (tab === 'events') return adminPaths.auditLog;
  if (tab === 'users' && userId) return adminPaths.user(userId);
  return adminPaths.users;
}
