import { useTranslation } from 'react-i18next';
import type { DocumentSource } from '@/types/document';
import { formatSourceTime, sourceKindLabel } from '@/lib/documents/documentExport';
import styles from './DocumentPanel.module.css';

/** 系统生成的数据来源；为空时说明本文档未引用实时查询结果。 */
export default function DocumentSources({ sources }: { sources: DocumentSource[] }) {
  const { t, i18n } = useTranslation();
  return (
    <section className={`${styles.sources} fdoc-sources`} data-testid="document-sources">
      <h2>{t('documents.sources.title')}</h2>
      {sources.length === 0 ? (
        <p>{t('documents.sources.empty')}</p>
      ) : (
        <ul>
          {sources.map((source, index) => {
            const time = formatSourceTime(source.fetched_at, i18n.language);
            return (
              <li key={`${source.kind}-${source.label}-${index}`}>
                <span>{sourceKindLabel(source.kind, t)}：</span>
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a>
                ) : (
                  <span>{source.label}</span>
                )}
                {source.provider ? <span> · {source.provider}</span> : null}
                {time ? <span>{t('documents.sources.queriedAt', { time })}</span> : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
