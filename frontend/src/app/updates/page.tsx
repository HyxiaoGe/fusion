import { Suspense } from 'react';
import ChangelogList from '@/components/changelogs/ChangelogList';

export default function UpdatesPage() {
  // 定位参数来自 useSearchParams，需要 Suspense 边界才能静态预渲染。
  return <Suspense fallback={null}><ChangelogList /></Suspense>;
}
