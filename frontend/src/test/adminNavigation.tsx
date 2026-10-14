import { forwardRef, useMemo, useSyncExternalStore, type AnchorHTMLAttributes } from 'react';
import { vi } from 'vitest';

/** 管理中心测试用的内存路由：按浏览器历史语义记录 push/replace/back；不依赖 next/navigation，可在 vi.mock 工厂里引用。 */
export const adminNavigation = {
  history: ['/admin/users'],
  index: 0,
  params: {} as Record<string, string>,
  listeners: new Set<() => void>(),
  push: vi.fn((url: string) => {
    adminNavigation.history = adminNavigation.history.slice(0, adminNavigation.index + 1);
    adminNavigation.history.push(url);
    adminNavigation.index += 1;
    adminNavigation.emit();
  }),
  replace: vi.fn((url: string) => {
    adminNavigation.history[adminNavigation.index] = url;
    adminNavigation.emit();
  }),
  back: vi.fn(() => {
    if (adminNavigation.index > 0) adminNavigation.index -= 1;
    adminNavigation.emit();
  }),
  get url() {
    return adminNavigation.history[adminNavigation.index];
  },
  get pathname() {
    return adminNavigation.url.split('?')[0];
  },
  get search() {
    return adminNavigation.url.split('?')[1] ?? '';
  },
  emit() {
    adminNavigation.listeners.forEach(listener => listener());
  },
  reset(url = '/admin/users', params: Record<string, string> = {}) {
    adminNavigation.history = [url];
    adminNavigation.index = 0;
    adminNavigation.params = params;
    adminNavigation.push.mockClear();
    adminNavigation.replace.mockClear();
    adminNavigation.back.mockClear();
    adminNavigation.emit();
  },
};

function subscribe(listener: () => void) {
  adminNavigation.listeners.add(listener);
  return () => adminNavigation.listeners.delete(listener);
}

function useCurrentUrl() {
  return useSyncExternalStore(subscribe, () => adminNavigation.url, () => adminNavigation.url);
}

/** 供 vi.mock('next/navigation', () => nextNavigationMock) 使用。 */
export const nextNavigationMock = {
  useRouter: () => ({ push: adminNavigation.push, replace: adminNavigation.replace, back: adminNavigation.back }),
  usePathname: () => useCurrentUrl().split('?')[0],
  useSearchParams: () => {
    const search = useCurrentUrl().split('?')[1] ?? '';
    return useMemo(() => new URLSearchParams(search), [search]);
  },
  useParams: () => adminNavigation.params,
  redirect: vi.fn(),
};

const MockLink = forwardRef<HTMLAnchorElement, AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }>(
  ({ href, onClick, ...props }, ref) => (
    <a
      ref={ref}
      href={href}
      {...props}
      onClick={event => {
        onClick?.(event);
        event.preventDefault();
        adminNavigation.push(href);
      }}
    />
  ),
);
MockLink.displayName = 'MockLink';

/** 供 vi.mock('next/link', () => nextLinkMock) 使用。 */
export const nextLinkMock = { default: MockLink };

