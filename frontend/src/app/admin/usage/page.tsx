import { AdminPanelHeader } from '@/components/admin/AdminPanelPrimitives';
import ServiceUsagePanel from '@/components/admin/operations/ServiceUsagePanel';

export default function AdminUsagePage() {
  return (
    <>
      <AdminPanelHeader title="服务用量" description="查看搜索与邮件服务的额度和近期调用量。" />
      <ServiceUsagePanel />
    </>
  );
}
