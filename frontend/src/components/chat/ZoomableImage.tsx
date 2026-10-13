'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';

export const MAX_ZOOM = 6;
const DOUBLE_CLICK_ZOOM = 2.5;
const DOUBLE_TAP_MS = 300;
const WHEEL_ZOOM_SPEED = 0.002;

interface ZoomTransform {
  scale: number;
  x: number;
  y: number;
}

export const IDENTITY_TRANSFORM: ZoomTransform = { scale: 1, x: 0, y: 0 };

/**
 * 以屏幕上的 anchor 点为中心缩放到 nextScale，保持该点下的图片内容不动。
 * center 是图片未变换时的中心点（屏幕坐标）；transform-origin 为中心。
 */
export function zoomAround(
  current: ZoomTransform,
  nextScale: number,
  anchor: { x: number; y: number },
  center: { x: number; y: number },
): ZoomTransform {
  const scale = Math.min(MAX_ZOOM, Math.max(1, nextScale));
  if (scale === 1) return IDENTITY_TRANSFORM;
  const localX = (anchor.x - center.x - current.x) / current.scale;
  const localY = (anchor.y - center.y - current.y) / current.scale;
  return {
    scale,
    x: anchor.x - center.x - scale * localX,
    y: anchor.y - center.y - scale * localY,
  };
}

interface ZoomableImageProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt: string;
}

/** 大图查看：双击在点击处放大/还原，滚轮或双指缩放，放大后拖动平移。 */
export default function ZoomableImage({ src, alt, className, style, onClick, ...imgProps }: ZoomableImageProps) {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [transform, setTransform] = useState<ZoomTransform>(IDENTITY_TRANSFORM);
  const [animate, setAnimate] = useState(false);
  const [dragging, setDragging] = useState(false);
  const transformRef = useRef(transform);
  const pointersRef = useRef(new Map<number, { x: number; y: number }>());
  const pinchRef = useRef<{ distance: number; scale: number } | null>(null);
  const lastTapRef = useRef(0);
  const lastPointerTypeRef = useRef('mouse');
  const tapStartRef = useRef<{ x: number; y: number; multi: boolean } | null>(null);

  const apply = useCallback((next: ZoomTransform, withAnimation = false) => {
    transformRef.current = next;
    setAnimate(withAnimation);
    setTransform(next);
  }, []);

  useEffect(() => {
    apply(IDENTITY_TRANSFORM);
  }, [src, apply]);

  const untransformedCenter = useCallback(() => {
    if (!image) return { x: 0, y: 0 };
    const rect = image.getBoundingClientRect();
    const current = transformRef.current;
    return { x: rect.left + rect.width / 2 - current.x, y: rect.top + rect.height / 2 - current.y };
  }, [image]);

  const toggleZoom = useCallback((anchor: { x: number; y: number }) => {
    const current = transformRef.current;
    if (current.scale > 1) {
      apply(IDENTITY_TRANSFORM, true);
      return;
    }
    const fitWidth = image?.getBoundingClientRect().width || 0;
    const naturalScale = image && fitWidth > 0 ? image.naturalWidth / fitWidth : 0;
    apply(zoomAround(current, Math.max(DOUBLE_CLICK_ZOOM, naturalScale), anchor, untransformedCenter()), true);
  }, [apply, image, untransformedCenter]);

  // React 的 onWheel 是 passive 监听，无法阻止页面滚动，这里手动挂非 passive 监听。
  useEffect(() => {
    if (!image) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const current = transformRef.current;
      const nextScale = current.scale * Math.exp(-event.deltaY * WHEEL_ZOOM_SPEED);
      apply(zoomAround(current, nextScale, { x: event.clientX, y: event.clientY }, untransformedCenter()));
    };
    image.addEventListener('wheel', handleWheel, { passive: false });
    return () => image.removeEventListener('wheel', handleWheel);
  }, [apply, image, untransformedCenter]);

  const handlePointerDown = (event: React.PointerEvent<HTMLImageElement>) => {
    lastPointerTypeRef.current = event.pointerType;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    tapStartRef.current = pointersRef.current.size === 1
      ? { x: event.clientX, y: event.clientY, multi: false }
      : tapStartRef.current && { ...tapStartRef.current, multi: true };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    if (pointersRef.current.size === 2) {
      const [a, b] = [...pointersRef.current.values()];
      pinchRef.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), scale: transformRef.current.scale };
    }
    setDragging(transformRef.current.scale > 1 || pointersRef.current.size === 2);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLImageElement>) => {
    const previous = pointersRef.current.get(event.pointerId);
    if (!previous) return;
    const point = { x: event.clientX, y: event.clientY };
    pointersRef.current.set(event.pointerId, point);
    const current = transformRef.current;
    if (pointersRef.current.size === 2 && pinchRef.current) {
      const [a, b] = [...pointersRef.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchRef.current.distance > 0) {
        const nextScale = pinchRef.current.scale * (distance / pinchRef.current.distance);
        apply(zoomAround(current, nextScale, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, untransformedCenter()));
      }
      return;
    }
    if (current.scale > 1) {
      apply({ ...current, x: current.x + point.x - previous.x, y: current.y + point.y - previous.y });
    }
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLImageElement>) => {
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 0) setDragging(false);
    // 触屏上 dblclick 不可靠，自己识别双击；拖动或双指操作不算一次点按。
    const tapStart = tapStartRef.current;
    const isTap = tapStart !== null && !tapStart.multi
      && Math.hypot(event.clientX - tapStart.x, event.clientY - tapStart.y) < 10;
    if (pointersRef.current.size === 0) tapStartRef.current = null;
    if (event.pointerType === 'touch' && event.type === 'pointerup' && isTap) {
      const now = event.timeStamp;
      if (now - lastTapRef.current < DOUBLE_TAP_MS) {
        lastTapRef.current = 0;
        toggleZoom({ x: event.clientX, y: event.clientY });
      } else {
        lastTapRef.current = now;
      }
    }
  };

  const zoomed = transform.scale > 1;
  return (
    // Lightbox 支持 blob 与鉴权图片地址，不能由 Next Image 服务端重新请求。
    // eslint-disable-next-line @next/next/no-img-element
    <img
      {...imgProps}
      ref={setImage}
      src={src}
      alt={alt}
      draggable={false}
      data-zoom-scale={transform.scale}
      className={`${className ?? ''} touch-none select-none ${zoomed ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : 'cursor-zoom-in'} ${animate ? 'transition-transform duration-200 ease-out motion-reduce:transition-none' : ''}`}
      style={{
        ...style,
        transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
        transformOrigin: 'center center',
      }}
      onClick={onClick}
      onDoubleClick={(event) => {
        event.stopPropagation();
        if (lastPointerTypeRef.current === 'touch') return;
        toggleZoom({ x: event.clientX, y: event.clientY });
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    />
  );
}
