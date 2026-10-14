'use client';

import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

export type AdminListValues<K extends string> = Record<K, string>;

/**
 * 管理列表的筛选与页码放在 URL 查询参数里：刷新、分享和浏览器返回都能还原列表。
 * 筛选与翻页使用 replace，不为每次筛选新增历史记录。
 */
export function useAdminListParams<K extends string>(keys: readonly K[]) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const search = searchParams.toString();

  // 只按筛选值变化生成新对象，翻页不会让调用方的筛选草稿被重置。
  const signature = useMemo(() => {
    const params = new URLSearchParams(search);
    return JSON.stringify(keys.map(key => params.get(key)?.trim() ?? ''));
  }, [keys, search]);
  const values = useMemo(() => {
    const parsed = JSON.parse(signature) as string[];
    return Object.fromEntries(keys.map((key, index) => [key, parsed[index]])) as AdminListValues<K>;
  }, [keys, signature]);

  const page = useMemo(() => {
    const parsed = Number(new URLSearchParams(search).get('page'));
    return Number.isInteger(parsed) && parsed > 1 ? parsed : 1;
  }, [search]);

  const navigate = useCallback((update: (params: URLSearchParams) => void) => {
    const params = new URLSearchParams(search);
    update(params);
    const next = params.toString();
    router.replace(next ? `${pathname}?${next}` : pathname, { scroll: false });
  }, [pathname, router, search]);

  const setFilters = useCallback((filters: Partial<AdminListValues<K>>) => {
    navigate(params => {
      for (const key of keys) {
        const value = filters[key]?.trim();
        if (value) params.set(key, value);
        else params.delete(key);
      }
      params.delete('page');
    });
  }, [keys, navigate]);

  const setPage = useCallback((nextPage: number) => {
    navigate(params => {
      if (nextPage > 1) params.set('page', String(nextPage));
      else params.delete('page');
    });
  }, [navigate]);

  return { values, page, setFilters, setPage };
}
