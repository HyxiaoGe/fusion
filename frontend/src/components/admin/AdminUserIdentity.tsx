import styles from './AdminSurface.module.css';

interface AdminUserIdentityValue {
  id: string;
  username: string;
  nickname: string | null;
  email_masked?: string | null;
}

export default function AdminUserIdentity({
  user,
  showEmail = true,
  compact = false,
}: {
  user: AdminUserIdentityValue;
  showEmail?: boolean;
  compact?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="break-words font-medium">{user.nickname || '未设置昵称'}</span>
        <span className="break-all text-xs text-muted-foreground">@{user.username}</span>
      </div>
      <div className={`mt-0.5 text-xs text-muted-foreground ${compact ? 'flex min-w-0 gap-1' : ''}`}><span className="shrink-0">用户 ID：</span><span className={compact ? styles.recordId : 'break-all'} title={user.id}>{user.id}</span></div>
      {showEmail ? <div className="mt-0.5 break-all text-xs text-muted-foreground">邮箱：{user.email_masked || '未采集'}</div> : null}
    </div>
  );
}
