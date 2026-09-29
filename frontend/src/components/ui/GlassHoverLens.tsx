import type { PointerEvent } from 'react';
import { cn } from '@/lib/utils';
import lensStyles from './GlassLens.module.css';
import surfaceStyles from './GlassSurface.module.css';

export function pointGlassLight(event: PointerEvent<HTMLElement>) {
  if (event.pointerType === 'touch') return;
  const bounds = event.currentTarget.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const x = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
  const y = Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height));
  const diagonal = (x + y) / 2;
  event.currentTarget.style.setProperty('--glint-x', `${x * bounds.width}px`);
  event.currentTarget.style.setProperty('--glint-y', `${y * bounds.height}px`);
  event.currentTarget.style.setProperty('--corner-tl', (0.48 + 0.42 * (1 - diagonal)).toFixed(2));
  event.currentTarget.style.setProperty('--corner-br', (0.48 + 0.42 * diagonal).toFixed(2));
}

export function resetGlassLight(event: PointerEvent<HTMLElement>) {
  event.currentTarget.style.removeProperty('--glint-x');
  event.currentTarget.style.removeProperty('--glint-y');
  event.currentTarget.style.removeProperty('--corner-tl');
  event.currentTarget.style.removeProperty('--corner-br');
}

export default function GlassHoverLens({ corners = true }: { corners?: boolean }) {
  return (
    <span className={cn(lensStyles.lens, surfaceStyles.hoverLens)} data-glass-lens aria-hidden="true">
      {corners ? (
        <>
          <span className={cn(lensStyles.corner, lensStyles.topLeft)} />
          <span className={cn(lensStyles.corner, lensStyles.bottomRight)} />
        </>
      ) : null}
    </span>
  );
}
