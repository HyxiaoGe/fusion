"use client";

import { MenuIcon, XIcon } from "lucide-react";
import { usePathname } from "next/navigation";
import React from "react";
import ErrorToastContainer from "../ui/error-toast";
import ResizableSidebar from "./ResizableSidebar";
import { UserAvatarMenu } from "./UserAvatarMenu";
import { Button } from "../ui/button";

interface MainLayoutProps {
  children: React.ReactNode;
  sidebar?: React.ReactNode;
  title?: string;
  header?: React.ReactNode;
  rightPanel?: React.ReactNode;
}

const MainLayout: React.FC<MainLayoutProps> = ({ children, sidebar, rightPanel }) => {
  const pathname = usePathname();
  // 首帧与服务端保持一致，挂载后再按实际宽度切换，避免移动端 hydration 失配。
  const [isMobileViewport, setIsMobileViewport] = React.useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = React.useState(false);

  React.useEffect(() => {
    const updateViewport = () => {
      const isMobile = window.innerWidth < 1024;
      setIsMobileViewport(isMobile);

      if (!isMobile) {
        setIsMobileSidebarOpen(false);
      }
    };

    updateViewport();
    window.addEventListener("resize", updateViewport);
    return () => window.removeEventListener("resize", updateViewport);
  }, []);

  React.useEffect(() => {
    setIsMobileSidebarOpen(false);
  }, [pathname]);

  return (
    <div className="h-screen flex flex-col">
      {/* 移动端简化 Header */}
      {isMobileViewport && (
        <header className="h-14 border-b flex items-center justify-between px-4 sticky top-0 z-10 bg-background">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-9 w-9"
            onClick={() => setIsMobileSidebarOpen(true)}
            aria-label="打开对话侧栏"
          >
            <MenuIcon className="h-5 w-5" />
          </Button>
          <span className="text-sm font-semibold text-foreground">Fusion AI</span>
          <div className="flex items-center">
            <UserAvatarMenu />
          </div>
        </header>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* 桌面端 Sidebar */}
        {sidebar && !isMobileViewport ? (
          <ResizableSidebar defaultWidth={320} minWidth={280} maxWidth={400}>
            {sidebar}
          </ResizableSidebar>
        ) : null}

        {/* 移动端 Sidebar drawer */}
        {sidebar && isMobileViewport && isMobileSidebarOpen ? (
          <div className="fixed inset-0 z-40 lg:hidden" aria-label="对话侧栏遮罩">
            <button
              type="button"
              className="absolute inset-0 bg-black/45"
              aria-label="关闭对话侧栏"
              onClick={() => setIsMobileSidebarOpen(false)}
            />
            <aside
              className="absolute inset-y-0 left-0 w-[min(85vw,320px)] border-r bg-bg-subtle shadow-xl"
              onClick={(event) => {
                // 通知通过 Portal 展示；按钮先完成导航，再收起遮罩，避免提前卸载吞掉点击。
                if (event.target instanceof Element && event.target.closest('[data-sidebar-navigation]')) {
                  setIsMobileSidebarOpen(false);
                }
              }}
            >
              <div className="absolute right-3 top-3 z-10">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 rounded-full"
                  onClick={() => setIsMobileSidebarOpen(false)}
                  aria-label="收起对话侧栏"
                >
                  <XIcon className="h-5 w-5" />
                </Button>
              </div>
              <div className="h-full">
                {sidebar}
              </div>
            </aside>
          </div>
        ) : null}

        <div className="flex flex-1 overflow-hidden">
          <main className="flex-1 overflow-y-auto">{children}</main>
          {rightPanel && (
            <aside className="w-[400px] border-l overflow-y-auto bg-background shadow-sm flex-shrink-0">
              {rightPanel}
            </aside>
          )}
        </div>
      </div>
      <ErrorToastContainer />
    </div>
  );
};

export default MainLayout;
