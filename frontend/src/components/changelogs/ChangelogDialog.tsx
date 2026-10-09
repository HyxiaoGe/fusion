'use client';

import { ScrollText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useNotifications } from '@/components/notifications/NotificationsProvider';
import { useAppDispatch, useAppSelector } from '@/redux/hooks';
import { closeChangelogDialog } from '@/redux/slices/settingsSlice';
import ChangelogTimeline from './ChangelogTimeline';

/** 更新日志与设置一样在当前页面上弹出，头像菜单和通知都只打开这个窗口，不离开对话。 */
export default function ChangelogDialog() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const { sessionKey } = useNotifications();
  const { isChangelogDialogOpen, focusChangelogId } = useAppSelector((state) => state.settings);
  return (
    <Dialog open={isChangelogDialogOpen} onOpenChange={(open) => { if (!open) dispatch(closeChangelogDialog()); }}>
      <DialogContent closeLabel={t('changelogs.close')} className="flex h-[85vh] w-full max-w-[95vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-[90vw] lg:max-w-5xl">
        <DialogHeader className="shrink-0 border-b px-6 py-4">
          <DialogTitle className="flex items-center gap-2"><ScrollText className="h-5 w-5" aria-hidden="true" />{t('changelogs.title')}</DialogTitle>
          <DialogDescription>{t('changelogs.description')}</DialogDescription>
        </DialogHeader>
        <ChangelogTimeline sessionKey={sessionKey} focusId={focusChangelogId} />
      </DialogContent>
    </Dialog>
  );
}
