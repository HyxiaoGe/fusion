'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowLeft, Boxes, MessagesSquare, Route, ScrollText, ShieldCheck, Users, type LucideIcon } from 'lucide-react';
import { adminPaths } from '@/lib/admin/adminRoutes';
import styles from './AdminSurface.module.css';

interface AdminNavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const ADMIN_NAV_GROUPS: Array<{ label: string; items: AdminNavItem[] }> = [
  {
    label: '审计',
    items: [
      { href: adminPaths.users, label: '用户', icon: Users },
      { href: adminPaths.conversations(), label: '对话', icon: MessagesSquare },
      { href: adminPaths.auditLog, label: '访问审计', icon: ScrollText },
    ],
  },
  {
    label: '模型',
    items: [
      { href: adminPaths.models, label: '模型运营', icon: Boxes },
      { href: adminPaths.itineraryQuality, label: '行程稳定性', icon: Route },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className={`flex h-screen flex-col overflow-hidden ${styles.shell}`}>
      <header className={`flex shrink-0 items-center justify-between px-4 lg:px-6 ${styles.shellHeader}`}>
        <div className="flex items-center gap-2 font-semibold">
          <ShieldCheck className={`h-5 w-5 ${styles.brandIcon}`} aria-hidden="true" />
          Fusion 管理中心
        </div>
        {/* 管理页使用独立 CSP，必须整页导航才能让浏览器切回普通页面 CSP。 */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a href="/chat/new" className={`flex items-center gap-1.5 text-sm text-muted-foreground ${styles.returnLink}`}>
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          返回聊天
        </a>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        <div className={`${styles.workspace} ${styles.controls}`}>
          <nav aria-label="管理中心导航" className={styles.navigation}>
            {ADMIN_NAV_GROUPS.map(group => (
              <div key={group.label} className={styles.navGroup}>
                <div className={styles.navGroupLabel}>{group.label}</div>
                {group.items.map(item => {
                  const active = isActive(pathname, item.href);
                  const Icon = item.icon;
                  return (
                    <Link key={item.href} href={item.href} className={styles.navLink} data-active={active ? 'true' : undefined} aria-current={active ? 'page' : undefined}>
                      <Icon aria-hidden="true" />{item.label}
                    </Link>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className={styles.panel}>{children}</div>
        </div>
      </main>
    </div>
  );
}
