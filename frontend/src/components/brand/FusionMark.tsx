import { cn } from '@/lib/utils';

interface FusionMarkProps {
  className?: string;
}

/**
 * Fusion 品牌标志，取 v2 定稿的 32px 专用轮廓。
 * 上段固定品牌蓝，下段跟随当前文字颜色，随页面内明暗主题切换。
 */
export function FusionMark({ className }: FusionMarkProps) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" className={cn('shrink-0', className)}>
      <path
        fill="#3B66F5"
        d="M11 3H26A3 3 0 0 1 29 6A3 3 0 0 1 26 9H13Q12 9 11.2 9.6L4.6 14.5Q3 15.6 3 13.6V8.5Q3 7 4.2 6.1L8.6 3.7Q9.5 3 11 3Z"
      />
      <path
        fill="currentColor"
        d="M12 14L24.776 17.423A3 3 0 0 1 23.224 23.219L10.259 19.745A1 1 0 0 0 9 20.711V26A3 3 0 0 1 3 26V20Q3 18.5 4.2 17.6L9.5 14.5Q10.7 13.65 12 14Z"
      />
    </svg>
  );
}
