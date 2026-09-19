"use client";

import { useAppDispatch, useAppSelector } from "@/redux/hooks";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { openSettingsDialog } from "@/redux/slices/settingsSlice";
import { logoutWithSso } from "@/redux/slices/authSlice";
import { resetConversationState } from "@/redux/slices/conversationSlice";
import { resetFileUploadState } from "@/redux/slices/fileUploadSlice";
import { resetStreamState } from "@/redux/slices/streamSlice";
import { Settings, LogOut, LogIn, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { LoginDialog } from "@/components/auth/LoginDialog";
import { DEFAULT_USER_AVATAR_SRC, proxiedAvatar } from "@/lib/auth/avatar";
import { useHasMounted } from "@/hooks/useHasMounted";

export function UserAvatarMenu() {
  const dispatch = useAppDispatch();
  const { isAuthenticated, user, sessionResolved, status: authStatus } = useAppSelector((state) => state.auth);
  const [isLoginDialogOpen, setIsLoginDialogOpen] = useState(false);
  // 登录态来自 localStorage，SSR 与首个 hydration 帧都无从得知 → getInitialAuthState 返回未登录，
  // 会先画死「登录」按钮再翻成头像（闪一帧）。hydration 完成前不渲染任何终态，只占同尺寸中性位。
  const hasMounted = useHasMounted();

  const getUserDisplayName = () => {
    if (isAuthenticated && user) {
      return user.nickname || user.username || '用户';
    }
    return '用户';
  };

  // 获取用户状态描述
  const getUserStatusText = () => {
    if (isAuthenticated && user) {
      return '已登录';
    }
    return '未登录';
  };

  const displayName = getUserDisplayName();
  const avatarSrc = proxiedAvatar(user?.avatar) ?? DEFAULT_USER_AVATAR_SRC;

  const handleOpenSettings = () => {
    dispatch(openSettingsDialog({}));
  };

  const handleLogout = () => {
    void dispatch(logoutWithSso());
    dispatch(resetConversationState());
    dispatch(resetStreamState());
    dispatch(resetFileUploadState());
  };

  const handleOpenLogin = () => {
    setIsLoginDialogOpen(true);
  };

  // 中性占位（同尺寸）出现在两种「尚不能下登出终态」的情形，避免闪出「登录」按钮：
  //  1) hydration 未完成：SSR/首帧读不到 localStorage 登录态（终态留给客户端定夺）。
  //  2) 已挂载但会话未定论且未登录：加载时本地无 token，正由静默 SSO / 刷新恢复会话——
  //     此窗口本质不是「登出」，若此刻画「登录」按钮，恢复完成翻成头像就成了「登录成功还闪一下登录按钮」。
  if (!hasMounted || (!isAuthenticated && !sessionResolved)) {
    return (
      <div
        data-testid="avatar-menu-placeholder"
        aria-hidden="true"
        className="h-9 w-9 rounded-full bg-muted/40"
      />
    );
  }

  return (
    <>
      {isAuthenticated ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="relative h-9 w-9 rounded-full hover:scale-110 transition-all duration-300 shadow-sm hover:shadow-md">
              <Avatar 
                key={`avatar-${isAuthenticated}-${user?.avatar}`}
                className="h-8 w-8"
              >
                <AvatarImage src={avatarSrc} alt={displayName} />
                <AvatarFallback className="bg-muted" />
              </Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-56" align="end" forceMount>
          {/* 用户信息 */}
          <DropdownMenuItem>
            <div className="flex items-center space-x-3">
              <div className="flex-shrink-0">
                <Avatar 
                  key={`menu-avatar-${isAuthenticated}-${user?.avatar}`}
                  className="h-8 w-8"
                >
                  <AvatarImage src={avatarSrc} alt={displayName} />
                  <AvatarFallback className="bg-muted" />
                </Avatar>
              </div>
              <div className="flex flex-col space-y-1">
                <p className="text-sm font-medium leading-none">{displayName}</p>
                <p className="text-xs leading-none text-muted-foreground">
                  {getUserStatusText()}
                </p>
              </div>
            </div>
          </DropdownMenuItem>
          
          <DropdownMenuSeparator />
          
          {/* 设置 */}
          <DropdownMenuItem onClick={handleOpenSettings} className="flex items-center cursor-pointer">
            <Settings className="mr-2 h-4 w-4" />
            <span>设置</span>
          </DropdownMenuItem>

          {authStatus === 'succeeded' && user?.is_superuser ? (
            <DropdownMenuItem asChild>
              {/* 管理中心使用独立 CSP，必须整页导航才能让 document 策略生效。 */}
              <a href="/admin" className="flex cursor-pointer items-center">
                <ShieldCheck className="mr-2 h-4 w-4" />
                <span>管理中心</span>
              </a>
            </DropdownMenuItem>
          ) : null}
          
          <DropdownMenuSeparator />
          
          {/* 登录状态相关功能 */}
          {isAuthenticated ? (
            <DropdownMenuItem onClick={handleLogout} className="flex items-center cursor-pointer text-red-600 dark:text-red-400">
              <LogOut className="mr-2 h-4 w-4" />
              <span>退出登录</span>
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onClick={handleOpenLogin} className="flex items-center cursor-pointer text-blue-600 dark:text-blue-400">
              <LogIn className="mr-2 h-4 w-4" />
              <span>立即登录</span>
            </DropdownMenuItem>
          )}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <Button
          variant="outline"
          onClick={handleOpenLogin}
          className="h-9 rounded-full px-3 text-sm shadow-sm"
        >
          <LogIn className="mr-2 h-4 w-4" />
          登录
        </Button>
      )}
      
      {/* 登录弹窗 */}
      <LoginDialog 
        open={isLoginDialogOpen} 
        onOpenChange={setIsLoginDialogOpen} 
      />
    </>
  );
} 
