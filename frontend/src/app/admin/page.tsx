import { redirect } from 'next/navigation';
import { legacyAdminPath } from '@/lib/admin/adminRoutes';

interface AdminIndexProps {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}

/** /admin 只做入口：默认进入用户列表，旧版 ?tab= 链接映射到对应路径。 */
export default async function AdminIndexPage({ searchParams }: AdminIndexProps) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries((await searchParams) ?? {})) {
    if (typeof value === 'string') params.set(key, value);
  }
  redirect(legacyAdminPath(params));
}
