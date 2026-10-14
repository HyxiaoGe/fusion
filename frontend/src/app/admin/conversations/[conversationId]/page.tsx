'use client';

import { useParams } from 'next/navigation';
import AdminConversationDetailView from '@/components/admin/AdminConversationDetailView';
import { AdminRouteNotFound } from '@/components/admin/AdminPanelPrimitives';
import { decodeAdminRouteSegment } from '@/lib/admin/adminRoutes';

export default function AdminConversationPage() {
  const conversationId = decodeAdminRouteSegment(useParams<{ conversationId: string }>().conversationId);
  return conversationId ? <AdminConversationDetailView key={conversationId} conversationId={conversationId} /> : <AdminRouteNotFound />;
}
