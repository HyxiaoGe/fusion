'use client';

import { Suspense, type ReactNode } from 'react';
import { AdminAccessProvider } from './AdminAccessContext';
import AdminGuard from './AdminGuard';
import AdminShell from './AdminShell';

export default function AdminFrame({ children }: { children: ReactNode }) {
  return (
    <AdminGuard>
      <AdminShell>
        <AdminAccessProvider>
          <Suspense fallback={<div role="status" className="text-sm text-muted-foreground">正在读取管理中心…</div>}>
            {children}
          </Suspense>
        </AdminAccessProvider>
      </AdminShell>
    </AdminGuard>
  );
}
