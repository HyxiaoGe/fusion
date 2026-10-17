'use client';

import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';

interface Particle {
  homeX: number;
  homeY: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  accent: boolean;
}

interface ParticleTextProps {
  text: string;
  label: string;
  className?: string;
}

const SPRING = 0.045;
const FRICTION = 0.84;
const ACCENT_RATIO = 0.08;

export function ParticleText({ text, label, className }: ParticleTextProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const pointer = { x: 0, y: 0, active: false };
    let particles: Particle[] = [];
    let width = 0;
    let height = 0;
    let frame = 0;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;

    const build = () => {
      const rect = canvas.getBoundingClientRect();
      width = Math.round(rect.width);
      height = Math.round(rect.height);
      if (width === 0 || height === 0) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const offscreen = document.createElement('canvas');
      offscreen.width = width;
      offscreen.height = height;
      const off = offscreen.getContext('2d');
      if (!off) return;
      off.textAlign = 'center';
      off.textBaseline = 'middle';
      off.font = `900 ${Math.floor(height * 0.92)}px ${getComputedStyle(canvas).fontFamily}`;
      off.fillText(text, width / 2, height * 0.54);
      const pixels = off.getImageData(0, 0, width, height).data;

      const gap = Math.max(4, Math.round(width / 150));
      const previous = particles;
      particles = [];
      for (let y = 0; y < height; y += gap) {
        for (let x = 0; x < width; x += gap) {
          if (pixels[(y * width + x) * 4 + 3] <= 128) continue;
          // 尺寸变化时沿用旧粒子的当前位置，让文字平滑重排而不是重新飞入。
          const prev = previous[particles.length];
          const startAtHome = reduceMotion && !prev;
          particles.push({
            homeX: x,
            homeY: y,
            x: prev?.x ?? (startAtHome ? x : Math.random() * width),
            y: prev?.y ?? (startAtHome ? y : Math.random() * height),
            vx: 0,
            vy: 0,
            radius: gap * (0.38 + Math.random() * 0.12),
            accent: Math.random() < ACCENT_RATIO,
          });
        }
      }
    };

    const tick = () => {
      // 每帧读取主题变量，用户切换明暗主题时粒子颜色立即跟随。
      const rootStyle = getComputedStyle(document.documentElement);
      const foreground = rootStyle.getPropertyValue('--foreground').trim();
      const accent = rootStyle.getPropertyValue('--info').trim();
      const repelRadius = Math.max(60, width / 9);

      ctx.clearRect(0, 0, width, height);
      for (const p of particles) {
        if (pointer.active) {
          const dx = p.x - pointer.x;
          const dy = p.y - pointer.y;
          const distSq = dx * dx + dy * dy;
          if (distSq < repelRadius * repelRadius) {
            const dist = Math.sqrt(distSq) || 1;
            const force = (1 - dist / repelRadius) * 6;
            p.vx += (dx / dist) * force;
            p.vy += (dy / dist) * force;
          }
        }
        p.vx = (p.vx + (p.homeX - p.x) * SPRING) * FRICTION;
        p.vy = (p.vy + (p.homeY - p.y) * SPRING) * FRICTION;
        p.x += p.vx;
        p.y += p.vy;

        ctx.fillStyle = p.accent && accent ? accent : foreground;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fill();
      }
      frame = requestAnimationFrame(tick);
    };

    const updatePointer = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      pointer.x = event.clientX - rect.left;
      pointer.y = event.clientY - rect.top;
      pointer.active = !reduceMotion;
    };
    const releasePointer = () => {
      pointer.active = false;
    };
    const burst = (event: PointerEvent) => {
      updatePointer(event);
      if (reduceMotion) return;
      for (const p of particles) {
        const dx = p.x - pointer.x;
        const dy = p.y - pointer.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (40 / Math.max(dist / 40, 1)) * (0.6 + Math.random() * 0.8);
        p.vx += (dx / dist) * force;
        p.vy += (dy / dist) * force;
      }
    };
    const scheduleBuild = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(build, 120);
    };

    canvas.addEventListener('pointermove', updatePointer);
    canvas.addEventListener('pointerdown', burst);
    canvas.addEventListener('pointerleave', releasePointer);
    canvas.addEventListener('pointercancel', releasePointer);
    window.addEventListener('resize', scheduleBuild);
    build();
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(resizeTimer);
      canvas.removeEventListener('pointermove', updatePointer);
      canvas.removeEventListener('pointerdown', burst);
      canvas.removeEventListener('pointerleave', releasePointer);
      canvas.removeEventListener('pointercancel', releasePointer);
      window.removeEventListener('resize', scheduleBuild);
    };
  }, [text]);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={label}
      className={cn('block h-full w-full cursor-crosshair touch-none', className)}
    />
  );
}
