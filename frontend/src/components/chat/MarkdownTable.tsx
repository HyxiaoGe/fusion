'use client';

import { useCallback, useEffect, useId, useRef, useState, type ComponentProps } from 'react';
import { ArrowLeftRight, ArrowUpDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import styles from './MarkdownTable.module.css';

export default function MarkdownTable(props: ComponentProps<'table'>) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const [overflow, setOverflow] = useState({ horizontal: false, vertical: false, left: false, right: false });

  const measure = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    const maxLeft = element.scrollWidth - element.clientWidth;
    const next = {
      horizontal: maxLeft > 1,
      vertical: element.scrollHeight - element.clientHeight > 1,
      left: element.scrollLeft > 1,
      right: maxLeft - element.scrollLeft > 1,
    };
    setOverflow(current => Object.keys(next).every(key => current[key as keyof typeof next] === next[key as keyof typeof next]) ? current : next);
  }, []);

  useEffect(() => {
    const element = scrollRef.current;
    const table = element?.querySelector('table');
    if (!element || !table) return;
    measure();
    // 观察表格自身：流式新增行、字体变化和侧栏开合都能刷新滚动提示。
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
    observer?.observe(element);
    observer?.observe(table);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure]);

  const scrollable = overflow.horizontal || overflow.vertical;
  const hintKey = overflow.horizontal && overflow.vertical ? 'both' : overflow.horizontal ? 'horizontal' : 'vertical';

  return (
    <div className={styles.frame}>
      <div className={styles.viewport} data-left={overflow.left} data-right={overflow.right}>
        <div
          ref={scrollRef}
          className={cn('overflow-x-auto', styles.scroll)}
          role="region"
          aria-label={t('chatBody.table.label')}
          aria-describedby={scrollable ? hintId : undefined}
          tabIndex={scrollable ? 0 : undefined}
          onScroll={measure}
        >
          <table {...props} />
        </div>
      </div>
      {scrollable ? (
        <div id={hintId} className={styles.hint}>
          {overflow.horizontal ? <ArrowLeftRight aria-hidden="true" /> : <ArrowUpDown aria-hidden="true" />}
          <span>{t(`chatBody.table.${hintKey}`)}</span>
        </div>
      ) : null}
    </div>
  );
}
