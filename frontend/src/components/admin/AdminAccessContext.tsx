'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { adminPaths } from '@/lib/admin/adminRoutes';

interface AdminAccessValue {
  /** 后端返回 401/403 时调用：卸载全部管理内容，敏感数据随组件一起清空。 */
  revoke: () => void;
  /** 站内打开过页面就走浏览器返回，直接深链进入则替换到父级页面。 */
  goBack: (fallbackHref: string) => void;
}

const AdminAccessContext = createContext<AdminAccessValue | null>(null);

export function AdminAccessProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [revoked, setRevoked] = useState(false);
  const entryPathRef = useRef(pathname);
  const navigatedRef = useRef(false);

  useEffect(() => {
    if (pathname !== entryPathRef.current) navigatedRef.current = true;
  }, [pathname]);

  const revoke = useCallback(() => {
    setRevoked(true);
    router.replace(adminPaths.users, { scroll: false });
  }, [router]);

  const goBack = useCallback((fallbackHref: string) => {
    if (navigatedRef.current) router.back();
    else router.replace(fallbackHref, { scroll: false });
  }, [router]);

  const value = useMemo(() => ({ revoke, goBack }), [goBack, revoke]);

  if (revoked) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center px-6">
        <div className="max-w-md rounded-xl border border-danger/30 bg-danger/5 p-8 text-center">
          <h1 className="font-semibold text-danger">管理员权限已失效</h1>
          <p className="mt-2 text-sm text-muted-foreground">敏感内容已从当前页面内存清空，请重新登录或联系系统管理员。</p>
        </div>
      </div>
    );
  }

  return <AdminAccessContext.Provider value={value}>{children}</AdminAccessContext.Provider>;
}

export function useAdminAccess(): AdminAccessValue {
  const value = useContext(AdminAccessContext);
  if (!value) throw new Error('useAdminAccess 必须在 AdminAccessProvider 内使用');
  return value;
}
