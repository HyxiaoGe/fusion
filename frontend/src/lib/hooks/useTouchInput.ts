'use client';

import { useEffect, useState } from 'react';

/** 与 Tailwind 的 touch: 变体和全局触屏样式一致：不能悬停、主要靠手指操作的设备。 */
export const TOUCH_INPUT_QUERY = '(hover: none) and (pointer: coarse)';

/**
 * 当前设备是否以触屏为主要输入方式（手机、平板）。
 * 服务端与首帧返回 false，挂载后按 matchMedia 更新，避免 hydration 失配。
 */
export function useTouchInput(): boolean {
  const [isTouch, setIsTouch] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const media = window.matchMedia(TOUCH_INPUT_QUERY);
    const update = () => setIsTouch(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return isTouch;
}
