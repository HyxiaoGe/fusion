"use client";

import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";

interface SettingsDialogFocusOptions {
  open: boolean;
  fallbackRef: RefObject<HTMLElement | null>;
}

function isAvailable(element: HTMLElement): boolean {
  if (!element.isConnected || element.matches(":disabled, [aria-disabled='true']")) return false;
  if (element.closest("[hidden], [inert], [aria-hidden='true'], [role='dialog'][data-state='closed']")) return false;
  const view = element.ownerDocument.defaultView;
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    const style = view?.getComputedStyle(node);
    if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse") return false;
  }
  return true;
}

// 受控设置弹窗没有 DialogTrigger，按本次真实入口恢复，避免退出动画的旧回调抢焦点。
export function useSettingsDialogFocus({ open, fallbackRef }: SettingsDialogFocusOptions) {
  const openRef = useRef(open);
  const mountedRef = useRef(false);
  const openerRef = useRef<HTMLElement | null>(null);
  const ownerDialogRef = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    openRef.current = open;
  }, [open]);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const captureOpener = useCallback((element: HTMLElement | null) => {
    openerRef.current = element;
    ownerDialogRef.current = element?.closest<HTMLElement>("[role='dialog']") ?? null;
  }, []);

  const onOpenAutoFocus = useCallback((event: Event) => {
    const content = event.currentTarget;
    if (!(content instanceof HTMLElement)) return;
    contentRef.current = content;
    const owner = ownerDialogRef.current ?? fallbackRef.current?.closest<HTMLElement>("[role='dialog']") ?? null;
    ownerDialogRef.current = owner === content ? null : owner;
  }, [fallbackRef]);

  const onCloseAutoFocus = useCallback((event: Event) => {
    event.preventDefault();
    const content = event.currentTarget;
    if (!mountedRef.current || openRef.current || content !== contentRef.current) return;
    const owner = ownerDialogRef.current;
    if (owner && (!owner.isConnected || owner.closest("[role='dialog'][data-state='closed']"))) return;

    const active = contentRef.current?.ownerDocument.activeElement;
    // 用户或较新的弹窗已经安置好焦点时，不覆盖它。
    if (active instanceof HTMLElement
      && active !== active.ownerDocument.body
      && active !== active.ownerDocument.documentElement
      && !active.hasAttribute("data-radix-focus-guard")
      && !contentRef.current?.contains(active)
      && isAvailable(active)) return;

    for (const target of [openerRef.current, fallbackRef.current]) {
      if (!target || !isAvailable(target)) continue;
      target.focus({ preventScroll: true });
      if (target.ownerDocument.activeElement === target) return;
    }
  }, [fallbackRef]);

  return { captureOpener, onOpenAutoFocus, onCloseAutoFocus };
}
