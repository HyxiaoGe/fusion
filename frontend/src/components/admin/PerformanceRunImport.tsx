'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import '@/lib/i18n';
import { Upload } from 'lucide-react';
import { importAdminPerformanceRun } from '@/lib/api/adminAudit';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import type { PerformanceRunImportPayload } from '@/types/adminAudit';
import { isAdminAccessError } from '@/lib/admin/adminAccess';
import { parsePerformanceRunImport, PerformanceRunImportError } from '@/lib/admin/performanceRunImport';
import styles from './AdminSurface.module.css';

export default function PerformanceRunImport({ onImported, onForbidden }: { onImported: () => void; onForbidden: () => void }) {
  const { t } = useTranslation();
  const [raw, setRaw] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [message, setMessage] = useState('');

  const handleImport = async () => {
    let candidate: PerformanceRunImportPayload;
    try {
      candidate = parsePerformanceRunImport(raw);
    } catch (error) {
      setStatus('error');
      setMessage(error instanceof PerformanceRunImportError ? error.message : t('admin.performanceImport.parseError'));
      return;
    }

    setStatus('loading');
    setMessage('');
    try {
      const result = await importAdminPerformanceRun(candidate);
      setStatus('success');
      setMessage(t(result.created ? 'admin.performanceImport.success' : 'admin.performanceImport.exists'));
      setRaw('');
      onImported();
    } catch (error) {
      if (isAdminAccessError(error)) {
        onForbidden();
        return;
      }
      setStatus('error');
      setMessage(error instanceof Error ? error.message : t('admin.performanceImport.importError'));
    }
  };

  return (
    <form className={styles.importPanel} aria-label={t('admin.performanceImport.title')} onSubmit={event => {
      event.preventDefault();
      if (raw.trim() && status !== 'loading') void handleImport();
    }}>
      <h3 className="flex items-center gap-2 font-medium">
        <Upload className="h-4 w-4 text-info" aria-hidden="true" />
        {t('admin.performanceImport.title')}
      </h3>
      <p className="mt-1 text-xs text-muted-foreground">{t('admin.performanceImport.description')}</p>
      <label className="mt-4 block text-sm font-medium" htmlFor="performance-run-json">{t('admin.performanceImport.jsonLabel')}</label>
      <Textarea
        id="performance-run-json"
        value={raw}
        disabled={status === 'loading'}
        onChange={event => setRaw(event.target.value)}
        className={styles.importEditor}
        placeholder={t('admin.performanceImport.placeholder')}
      />
      <div className={styles.importFooter}>
        <Button type="submit" disabled={!raw.trim() || status === 'loading'}>
          {t(status === 'loading' ? 'admin.performanceImport.loading' : 'admin.performanceImport.submit')}
        </Button>
        {message ? (
          <span className={status === 'error' ? 'text-sm text-danger' : 'text-sm text-success'} role="status">
            {message}
          </span>
        ) : null}
      </div>
    </form>
  );
}
