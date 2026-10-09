'use client';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { chatStore, settingsStore } from '@/lib/db/chatStore';
import { importDataFromFile } from '@/lib/db/importData';
import { useAppDispatch } from '@/redux/hooks';
import { AlertCircleIcon, CheckCircleIcon, DownloadIcon, UploadIcon } from 'lucide-react';
import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';
import glassSurface from '@/components/ui/GlassSurface.module.css';
import styles from '@/components/settings/SettingsSurface.module.css';
import { cn } from '@/lib/utils';

const DataManagement: React.FC = () => {
  const dispatch = useAppDispatch();
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  // 导出所有数据
  const handleExport = async () => {
    try {
      setIsLoading(true);
      setMessage(null);
      
      // 获取所有聊天记录和设置
      const chats = await chatStore.getAllChats();
      const settings = await settingsStore.getAllSettings();
      
      // 创建导出数据
      const exportData = {
        chats,
        settings
      };
      
      // 创建下载链接
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      
      // 执行下载
      const a = document.createElement('a');
      a.href = url;
      a.download = `ai-assistant-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      
      setMessage({ text: t('settings.data.exportSuccess'), type: 'success' });
    } catch (error) {
      console.error('导出数据失败:', error);
      setMessage({ text: t('settings.data.exportFailed'), type: 'error' });
    } finally {
      setIsLoading(false);
    }
  };

  // 点击导入按钮
  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  // 处理文件选择
  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const file = files[0];
    if (file.type !== 'application/json') {
      setMessage({ text: t('settings.data.invalidFile'), type: 'error' });
      return;
    }

    try {
      setIsLoading(true);
      setMessage(null);
      
      // 导入数据
      const result = await importDataFromFile(file, dispatch);
      setMessage({ text: result, type: 'success' });
    } catch (error) {
      console.error('导入数据失败:', error);
      setMessage({ text: t('settings.data.importFailed'), type: 'error' });
    } finally {
      setIsLoading(false);
      // 重置文件输入，以便可以重新选择同一个文件
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  return (
    <Card className="w-full" aria-busy={isLoading}>
      <CardHeader className="border-b pb-5">
        <CardTitle>{t('settings.data.title')}</CardTitle>
        <CardDescription>
          {t('settings.data.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {message && (
          <div className={styles.dataNotice} data-state={message.type} role={message.type === 'error' ? 'alert' : 'status'}
          >
            {message.type === 'success' ? (
              <CheckCircleIcon className="h-5 w-5 flex-shrink-0" />
            ) : (
              <AlertCircleIcon className="h-5 w-5 flex-shrink-0" />
            )}
            <span>{message.text}</span>
          </div>
        )}

        <div className={styles.dataGrid}>
          <div className={styles.dataAction}>
            <span className={styles.dataIcon}><DownloadIcon className="h-5 w-5" aria-hidden="true" /></span>
            <h3>{t('settings.data.export')}</h3>
            <p>{t('settings.data.exportDescription')}</p>
            <Button 
              onClick={handleExport} 
              disabled={isLoading}
              className={cn(glassSurface.surface, glassSurface.pill, glassSurface.interactive, styles.quietButton)}
              variant="outline"
              onPointerMove={pointGlassLight}
              onPointerLeave={resetGlassLight}
            >
              <GlassHoverLens />
              <DownloadIcon className="h-4 w-4" aria-hidden="true" />
              <span>{t('settings.data.export')}</span>
            </Button>
          </div>

          <div className={styles.dataAction}>
            <span className={styles.dataIcon}><UploadIcon className="h-5 w-5" aria-hidden="true" /></span>
            <h3>{t('settings.data.import')}</h3>
            <p>
              {t('settings.data.importDescription')}<span className={styles.dataWarning}>{t('settings.data.importWarning')}</span>
            </p>
            <Button 
              onClick={handleImportClick} 
              disabled={isLoading}
              className={cn(glassSurface.surface, glassSurface.pill, glassSurface.interactive, styles.quietButton)}
              variant="outline"
              onPointerMove={pointGlassLight}
              onPointerLeave={resetGlassLight}
            >
              <GlassHoverLens />
              <UploadIcon className="h-4 w-4" aria-hidden="true" />
              <span>{t('settings.data.import')}</span>
            </Button>
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              accept="application/json"
              className="hidden"
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default DataManagement;
