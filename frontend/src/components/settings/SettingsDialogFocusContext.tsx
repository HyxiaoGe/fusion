"use client";

import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode, type RefObject } from "react";

interface SettingsDialogOpener {
  openerRef: RefObject<HTMLElement | null>;
  captureOpener: (element: HTMLElement | null) => void;
}

const SettingsDialogFocusContext = createContext<SettingsDialogOpener | null>(null);

export function SettingsDialogFocusProvider({ children }: { children: ReactNode }) {
  const openerRef = useRef<HTMLElement | null>(null);
  const captureOpener = useCallback((element: HTMLElement | null) => { openerRef.current = element; }, []);
  const value = useMemo(() => ({ openerRef, captureOpener }), [captureOpener]);
  return <SettingsDialogFocusContext.Provider value={value}>{children}</SettingsDialogFocusContext.Provider>;
}

// 单独渲染设置组件时保留局部入口，生产页面由同一 provider 连接头像入口和设置弹窗。
export function useSettingsDialogOpener(): SettingsDialogOpener {
  const context = useContext(SettingsDialogFocusContext);
  const openerRef = useRef<HTMLElement | null>(null);
  const captureOpener = useCallback((element: HTMLElement | null) => { openerRef.current = element; }, []);
  const local = useMemo(() => ({ openerRef, captureOpener }), [captureOpener]);
  return context ?? local;
}
