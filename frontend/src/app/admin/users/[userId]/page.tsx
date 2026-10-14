'use client';

import { useParams } from 'next/navigation';
import AdminUserDetailView from '@/components/admin/AdminUserDetailView';
import { AdminRouteNotFound } from '@/components/admin/AdminPanelPrimitives';
import { decodeAdminRouteSegment } from '@/lib/admin/adminRoutes';

export default function AdminUserPage() {
  const userId = decodeAdminRouteSegment(useParams<{ userId: string }>().userId);
  return userId ? <AdminUserDetailView key={userId} userId={userId} /> : <AdminRouteNotFound />;
}
