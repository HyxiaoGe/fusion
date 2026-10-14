import { describe, expect, it } from 'vitest';
import { adminPaths, decodeAdminRouteSegment, legacyAdminPath, normalizeAdminRouteId } from './adminRoutes';

describe('adminRoutes', () => {
  it('详情路径编码 ID，列表筛选忽略不安全值', () => {
    expect(adminPaths.user('user / 1')).toBe('/admin/users/user%20%2F%201');
    expect(adminPaths.model('openrouter/gpt-5')).toBe('/admin/models/openrouter%2Fgpt-5');
    expect(adminPaths.conversations({ userId: ' user-1 ', modelId: 'bad\nvalue' })).toBe('/admin/conversations?user_id=user-1');
    expect(adminPaths.conversations()).toBe('/admin/conversations');
  });

  it('动态段解码后校验，非法编码按原值处理', () => {
    expect(decodeAdminRouteSegment('openrouter%2Fgpt-5')).toBe('openrouter/gpt-5');
    expect(decodeAdminRouteSegment('100%')).toBe('100%');
    expect(decodeAdminRouteSegment('x'.repeat(201))).toBeUndefined();
    expect(decodeAdminRouteSegment(['a'])).toBeUndefined();
    expect(normalizeAdminRouteId('user%0Asecret')).toBe('user%0Asecret');
    expect(decodeAdminRouteSegment('user%0Asecret')).toBeUndefined();
  });

  it.each([
    ['', '/admin/users'],
    ['tab=unknown&user_id=secret', '/admin/users'],
    ['user_id=user-1', '/admin/users'],
    ['tab=users&user_id=user-1', '/admin/users/user-1'],
    ['tab=conversations&user_id=user-1&conversation_id=conv-1', '/admin/conversations/conv-1'],
    ['tab=conversations&model_id=model-1', '/admin/conversations?model_id=model-1'],
    ['tab=models&model_id=%20model-1%20', '/admin/models/model-1'],
    ['tab=models', '/admin/models'],
    ['tab=events', '/admin/audit-log'],
    ['tab=performance&run_id=perf-1', '/admin/users'],
  ])('旧链接 %s → %s', (search, expected) => {
    expect(legacyAdminPath(new URLSearchParams(search))).toBe(expected);
  });
});
