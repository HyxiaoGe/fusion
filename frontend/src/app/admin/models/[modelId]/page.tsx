'use client';

import { useParams } from 'next/navigation';
import { AdminModelDetailView } from '@/components/admin/AdminModelsPanel';
import { AdminRouteNotFound } from '@/components/admin/AdminPanelPrimitives';
import { decodeAdminRouteSegment } from '@/lib/admin/adminRoutes';

export default function AdminModelPage() {
  const modelId = decodeAdminRouteSegment(useParams<{ modelId: string }>().modelId);
  return modelId ? <AdminModelDetailView key={modelId} modelId={modelId} /> : <AdminRouteNotFound />;
}
