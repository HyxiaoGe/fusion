"use client";

import MainLayout from "@/components/layouts/MainLayout";
import KnowledgeBaseManager from "@/components/settings/KnowledgeBaseManager";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAppSelector } from "@/redux/hooks";
import { Activity, BookOpen, Bot, Database, Network, SlidersHorizontal, Sparkles } from "lucide-react";
import DataManagement from "./DataManagement";
import McpServerManager from "./McpServerManager";
import ModelManagementPanel from "./ModelManagementPanel";
import RuntimeConfigManager from "./RuntimeConfigManager";
import ServiceUsagePanel from "./ServiceUsagePanel";
import SystemPrompt from "./SystemPrompt";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "@/components/settings/SettingsSurface.module.css";
import { revealFocusedSettingsTab } from "@/components/settings/settingsNavigation";

export default function SettingsPage() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState("general");
  const [isMounted, setIsMounted] = useState(false);
  const isAdmin = useAppSelector((state) => Boolean(state.auth.user?.is_superuser));
  const showAdminTabs = isMounted && isAdmin;
  const knowledgeBaseTitle = isMounted ? t("knowledgeBase.title") : "知识库";
  const knowledgeBaseShortTitle = isMounted ? t("knowledgeBase.shortTitle") : "知识";

  useEffect(() => {
    setIsMounted(true);
  }, []);

  return (
    <MainLayout>
      <div className={`${styles.workspace} ${styles.page}`}>
        <header className={styles.pageHeading}>
          <h1>{t("settings.title")}</h1>
          <p>{t("settings.description")}</p>
        </header>
        <Tabs value={activeTab} onValueChange={setActiveTab} className={styles.tabs}>
          <div data-testid="settings-tabs-scroller" className={`${styles.navigation} overflow-x-auto`} onFocusCapture={revealFocusedSettingsTab}>
            <TabsList aria-label={t("settings.navigation")} className={`${styles.navList} grid w-full gap-1`}>
              <TabsTrigger value="general" className={styles.navTrigger}>
                <Sparkles className="h-4 w-4" />
                <span className="hidden md:inline">{t("settings.tabs.personalization")}</span>
                <span className="md:hidden">{t("settings.tabs.personalizationShort")}</span>
              </TabsTrigger>
              <TabsTrigger value="data" className={styles.navTrigger}>
                <Database className="h-4 w-4" />
                <span className="hidden md:inline">{t("settings.tabs.data")}</span>
                <span className="md:hidden">{t("settings.tabs.dataShort")}</span>
              </TabsTrigger>
              <TabsTrigger value="knowledge" className={styles.navTrigger}>
                <BookOpen className="h-4 w-4" />
                <span className="hidden md:inline">{knowledgeBaseTitle}</span>
                <span className="md:hidden">{knowledgeBaseShortTitle}</span>
              </TabsTrigger>
              {showAdminTabs && (
                <>
                  <TabsTrigger value="usage" className={styles.navTrigger}>
                    <Activity className="h-4 w-4" />
                    <span className="hidden md:inline">{t("settings.tabs.usage")}</span>
                    <span className="md:hidden">{t("settings.tabs.usageShort")}</span>
                  </TabsTrigger>
                  <TabsTrigger value="runtime-config" className={styles.navTrigger}>
                    <SlidersHorizontal className="h-4 w-4" />
                    <span className="hidden md:inline">{t("settings.tabs.runtime")}</span>
                    <span className="md:hidden">{t("settings.tabs.runtimeShort")}</span>
                  </TabsTrigger>
                  <TabsTrigger value="mcp-servers" className={styles.navTrigger}>
                    <Network className="h-4 w-4" />
                    <span className="hidden md:inline">{t("settings.tabs.mcp")}</span>
                    <span className="md:hidden">{t("settings.tabs.mcpShort")}</span>
                  </TabsTrigger>
                  <TabsTrigger value="model-management" className={styles.navTrigger}>
                    <Bot className="h-4 w-4" />
                    <span className="hidden md:inline">{t("settings.tabs.models")}</span>
                    <span className="md:hidden">{t("settings.tabs.modelsShort")}</span>
                  </TabsTrigger>
                </>
              )}
            </TabsList>
          </div>

          <TabsContent value="general" className={`${styles.panel} ${styles.formPanel} space-y-6`}>
            <div>
              <SystemPrompt />
            </div>
          </TabsContent>

          <TabsContent value="data" className={`${styles.panel} ${styles.formPanel} space-y-6`}>
            <DataManagement />
          </TabsContent>

          <TabsContent value="knowledge" className={`${styles.panel} space-y-6`}>
            <KnowledgeBaseManager />
          </TabsContent>

          {showAdminTabs && (
            <>
              <TabsContent value="usage" className={`${styles.panel} space-y-6`}>
                <ServiceUsagePanel />
              </TabsContent>

              <TabsContent value="runtime-config" className={`${styles.panel} space-y-6`}>
                <RuntimeConfigManager />
              </TabsContent>

              <TabsContent value="mcp-servers" className={`${styles.panel} space-y-6`}>
                <McpServerManager />
              </TabsContent>

              <TabsContent value="model-management" className={`${styles.panel} space-y-6`}>
                <ModelManagementPanel />
              </TabsContent>
            </>
          )}
        </Tabs>
      </div>
    </MainLayout>
  );
}
