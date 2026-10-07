'use client';

import { useParams } from 'next/navigation';
import ChangelogReader from '@/components/changelogs/ChangelogReader';

export default function UpdateDetailPage() {
  const { changelogId } = useParams<{ changelogId: string }>();
  return <ChangelogReader changelogId={changelogId} />;
}
