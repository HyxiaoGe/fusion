"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAppDispatch, useAppSelector } from "@/redux/hooks";
import { selectIsGuest } from "@/redux/selectors";
import { closeSettingsDialog, setActiveSettingsTab } from "@/redux/slices/settingsSlice";
import { setThemeMode } from "@/redux/slices/themeSlice";
import { BookOpen, Brain, Settings, Sun, Moon, Laptop } from "lucide-react";
import KnowledgeBaseManager from "@/components/settings/KnowledgeBaseManager";
import MemoryPanel from "@/components/settings/panels/MemoryPanel";
import SystemPrompt from "@/components/settings/panels/SystemPrompt";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import GlassHoverLens, { pointGlassLight, resetGlassLight } from "@/components/ui/GlassHoverLens";
import glassSurface from "@/components/ui/GlassSurface.module.css";
import { cn } from "@/lib/utils";
import styles from "./SettingsSurface.module.css";
import { revealFocusedSettingsTab } from "./settingsNavigation";
import { useSettingsDialogOpener } from "./SettingsDialogFocusContext";
import { useSettingsDialogFocus } from "./useSettingsDialogFocus";
import { track } from "@/lib/analytics/analytics";

const THEME_MODES = [{ mode: 'light', Icon: Sun }, { mode: 'dark', Icon: Moon }, { mode: 'system', Icon: Laptop }] as const;

export const SettingsDialog = () => {
  const { t } = useTranslation();
  const themeId = useId();
  const dispatch = useAppDispatch();
  const { isSettingsDialogOpen, activeSettingsTab } = useAppSelector((state) => state.settings);
  const { openerRef } = useSettingsDialogOpener();
  const dialogFocus = useSettingsDialogFocus({ open: isSettingsDialogOpen, fallbackRef: openerRef });
  const { mode } = useAppSelector((state) => state.theme);
  const [isMounted, setIsMounted] = useState(false);
  // 记忆只对登录用户整理和使用；游客不显示记忆页。
  const isGuest = useAppSelector(selectIsGuest);
  const isAuthenticated = useAppSelector((state) => state.auth.isAuthenticated);
  const showMemoryTab = isMounted && isAuthenticated && !isGuest;
  const knowledgeBaseTitle = isMounted ? t("knowledgeBase.title") : "知识库";
  const knowledgeBaseShortTitle = isMounted ? t("knowledgeBase.shortTitle") : "知识";
  const visibleTabs = ["general", ...(showMemoryTab ? ["memory"] : []), "knowledge"];
  const selectedSettingsTab = visibleTabs.includes(activeSettingsTab) ? activeSettingsTab : "general";

  const handleClose = () => {
    dispatch(closeSettingsDialog());
  };

  useEffect(() => {
    setIsMounted(true);
  }, []);

  useEffect(() => {
    if (isSettingsDialogOpen) track('settings_opened', { panel: selectedSettingsTab });
  }, [isSettingsDialogOpen, selectedSettingsTab]);

  const handleTabChange = (tab: string) => {
    dispatch(setActiveSettingsTab(tab));
  };

  const handleThemeChange = (themeMode: 'light' | 'dark' | 'system') => {
    dispatch(setThemeMode(themeMode));
  };

  return (
    <Dialog open={isSettingsDialogOpen} onOpenChange={handleClose}>
      <DialogContent closeLabel={t("settings.close")} className={`${styles.workspace} ${styles.dialog} max-w-[95vw] w-full h-[85vh] flex flex-col sm:max-w-[90vw] lg:max-w-6xl xl:max-w-7xl`}
        onOpenAutoFocus={(event) => { dialogFocus.captureOpener(openerRef.current); dialogFocus.onOpenAutoFocus(event); }}
        onCloseAutoFocus={dialogFocus.onCloseAutoFocus}>
        <DialogHeader className={styles.dialogHeading}>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            {t("settings.title")}
          </DialogTitle>
          <DialogDescription className={styles.dialogDescription}>
            {t("settings.description")}
          </DialogDescription>
        </DialogHeader>

        <div className={styles.dialogBody}>
          <Tabs value={selectedSettingsTab} onValueChange={handleTabChange} className={`${styles.tabs} h-full`}>
            <div data-testid="settings-tabs-scroller" className={`${styles.navigation} overflow-x-auto`} onFocusCapture={revealFocusedSettingsTab}>
              <TabsList aria-label={t("settings.navigation")} className={`${styles.navList} grid w-full gap-1`}>
                <TabsTrigger value="general" className={styles.navTrigger}>
                  <Settings className="h-4 w-4" />
                  <span className="hidden md:inline">{t("settings.tabs.general")}</span>
                  <span className="md:hidden">{t("settings.tabs.generalShort")}</span>
                </TabsTrigger>
                {showMemoryTab && (
                  <TabsTrigger value="memory" className={styles.navTrigger}>
                    <Brain className="h-4 w-4" />
                    <span className="hidden md:inline">{t("settings.tabs.memory")}</span>
                    <span className="md:hidden">{t("settings.tabs.memoryShort")}</span>
                  </TabsTrigger>
                )}
                <TabsTrigger value="knowledge" className={styles.navTrigger}>
                  <BookOpen className="h-4 w-4" />
                  <span className="hidden md:inline">{knowledgeBaseTitle}</span>
                  <span className="md:hidden">{knowledgeBaseShortTitle}</span>
                </TabsTrigger>
              </TabsList>
            </div>

            {/* 常规设置标签页 */}
            <TabsContent value="general" className={`${styles.panel} ${styles.formPanel} space-y-6`}>
              <div>
                <Card>
                  <CardHeader className="border-b pb-5">
                    <CardTitle className="flex items-center gap-2">
                      <Sun className="h-5 w-5 text-primary" aria-hidden="true" />
                      {t("settings.appearance.title")}
                    </CardTitle>
                    <p className={styles.dialogDescription}>{t("settings.appearance.description")}</p>
                  </CardHeader>
                  <CardContent className={styles.themeContent}>
                    <div className="space-y-3" role="group" aria-label={t("settings.appearance.label")}>
                      <div className={styles.themeGrid}>
                        {THEME_MODES.map(({ mode: themeMode, Icon }) => (
                          <button
                            key={themeMode}
                            type="button"
                            aria-label={t(`settings.appearance.${themeMode}`)}
                            aria-describedby={`${themeId}-${themeMode}`}
                            aria-pressed={mode === themeMode}
                            onClick={() => handleThemeChange(themeMode)}
                            onPointerMove={pointGlassLight}
                            onPointerLeave={resetGlassLight}
                            className={cn(glassSurface.surface, glassSurface.card, glassSurface.interactive, styles.themeOption, mode === themeMode && glassSurface.selected)}
                          >
                            <GlassHoverLens />
                            <span className={styles.themeCopy}>
                              <Icon className="h-6 w-6" aria-hidden="true" />
                              <span className="font-medium">{t(`settings.appearance.${themeMode}`)}</span>
                              <span id={`${themeId}-${themeMode}`} className={styles.themeDescription}>{t(`settings.appearance.${themeMode}Hint`)}</span>
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </div>

              <div>
                <SystemPrompt />
              </div>
            </TabsContent>

            {showMemoryTab && (
              <TabsContent value="memory" className={`${styles.panel} ${styles.formPanel}`}>
                <MemoryPanel />
              </TabsContent>
            )}

            <TabsContent value="knowledge" className={styles.panel}>
              <KnowledgeBaseManager />
            </TabsContent>

          </Tabs>
        </div>
      </DialogContent>
    </Dialog>
  );
};
