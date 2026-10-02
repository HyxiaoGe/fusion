import type { FocusEvent } from "react";

// 焦点与选中字重更新后，只调整设置导航自己的水平位置。
export function revealFocusedSettingsTab(event: FocusEvent<HTMLDivElement>) {
  const tab = event.target;
  const scroller = event.currentTarget;
  if (!(tab instanceof HTMLElement) || tab.getAttribute("role") !== "tab") return;

  requestAnimationFrame(() => {
    if (!scroller.isConnected || document.activeElement !== tab) return;
    const viewport = scroller.getBoundingClientRect();
    const bounds = tab.getBoundingClientRect();
    const left = viewport.left + scroller.clientLeft;
    const right = left + scroller.clientWidth;
    const delta = bounds.width > scroller.clientWidth || bounds.left < left
      ? bounds.left - left
      : Math.max(0, bounds.right - right);
    if (delta !== 0) scroller.scrollBy({ left: delta, behavior: "instant" });
  });
}
