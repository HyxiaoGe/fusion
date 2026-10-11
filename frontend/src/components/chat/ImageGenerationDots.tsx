'use client';

import { useEffect, useRef } from 'react';

interface ImageGenerationDotsProps {
  /** 已用秒数：光斑随耗时扩大，只营造渐进感，不代表真实进度。 */
  elapsedSeconds: number;
  /** 生成中才有光斑和波纹；失败/中断时只留暗点。 */
  active: boolean;
}

const SPACING = 12;
const FRAME_INTERVAL_MS = 1000 / 30;

function resolveColor(canvas: HTMLCanvasElement, variable: string, fallback: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${variable})`;
  probe.style.display = 'none';
  canvas.parentElement?.appendChild(probe);
  const color = getComputedStyle(probe).color || fallback;
  probe.remove();
  return color;
}

/**
 * 生图占位点阵：每个点按位置错开明暗，形成斜向扫过的波纹；
 * 一块主色光斑在框内游走，范围随耗时扩大。
 */
export default function ImageGenerationDots({ elapsedSeconds, active }: ImageGenerationDotsProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const elapsedRef = useRef(elapsedSeconds);

  useEffect(() => {
    elapsedRef.current = elapsedSeconds;
  }, [elapsedSeconds]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const baseColor = resolveColor(canvas, '--muted-foreground', 'rgb(140,140,140)');
    const glowColor = resolveColor(canvas, '--info', 'rgb(59,110,230)');
    let width = 0;
    let height = 0;
    let frame = 0;
    let lastDrawn = 0;
    const startedAt = performance.now();

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const draw = (now: number) => {
      const t = reducedMotion ? 0 : (now - startedAt) / 1000;
      context.clearRect(0, 0, width, height);
      const columns = Math.floor(width / SPACING);
      const rows = Math.floor(height / SPACING);
      const offsetX = (width - (columns - 1) * SPACING) / 2;
      const offsetY = (height - (rows - 1) * SPACING) / 2;
      const spotX = 0.5 + 0.32 * Math.sin(t * 0.9);
      const spotY = 0.5 + 0.32 * Math.cos(t * 0.7);
      const spread = 0.16 + 0.22 * (1 - Math.exp(-elapsedRef.current / 20));
      const twoSpreadSq = 2 * spread * spread;

      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const x = offsetX + column * SPACING;
          const y = offsetY + row * SPACING;
          context.globalAlpha = 0.28;
          context.fillStyle = baseColor;
          context.beginPath();
          context.arc(x, y, 1.1, 0, Math.PI * 2);
          context.fill();
          if (!active) continue;

          const u = x / width;
          const v = y / height;
          const distanceSq = (u - spotX) ** 2 + (v - spotY) ** 2;
          const spot = Math.exp(-distanceSq / twoSpreadSq);
          const wave = 0.5 + 0.5 * Math.sin((u * 0.8 + v) * Math.PI * 2.4 - t * 2.6);
          const intensity = Math.min(1, 0.08 + 0.6 * spot + 0.45 * wave * (0.25 + 0.75 * spot));
          if (intensity < 0.06) continue;
          context.globalAlpha = intensity;
          context.fillStyle = glowColor;
          context.beginPath();
          context.arc(x, y, 1 + 1.4 * intensity, 0, Math.PI * 2);
          context.fill();
        }
      }
      context.globalAlpha = 1;
    };

    const loop = (now: number) => {
      if (now - lastDrawn >= FRAME_INTERVAL_MS) {
        lastDrawn = now;
        draw(now);
      }
      frame = window.requestAnimationFrame(loop);
    };

    resize();
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => {
        resize();
        draw(performance.now());
      });
    observer?.observe(canvas);

    if (active && !reducedMotion) {
      frame = window.requestAnimationFrame(loop);
    } else {
      draw(performance.now());
    }

    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [active]);

  return <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />;
}
