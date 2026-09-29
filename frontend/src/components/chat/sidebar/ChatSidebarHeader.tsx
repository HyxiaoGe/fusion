import React from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SquarePen } from "lucide-react";
import glassStyles from "@/components/ui/GlassLens.module.css";
import selectableStyles from "@/components/ui/GlassSelectable.module.css";

interface ChatSidebarHeaderProps {
  onNewChat: () => void;
  isNewChatActive?: boolean;
}

const ChatSidebarHeader: React.FC<ChatSidebarHeaderProps> = ({ onNewChat, isNewChatActive = false }) => {
  const handlePointerMove = (event: React.PointerEvent<HTMLButtonElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--glint-x", `${event.clientX - bounds.left}px`);
    event.currentTarget.style.setProperty("--glint-y", `${event.clientY - bounds.top}px`);
  };

  const handlePointerLeave = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.currentTarget.style.removeProperty("--glint-x");
    event.currentTarget.style.removeProperty("--glint-y");
  };

  return (
    <div className="flex flex-col gap-3 px-3 pb-3 pt-4">
      <div className="flex items-center justify-between px-2">
        <span className="text-base font-semibold tracking-tight text-foreground">Fusion AI</span>
        <span className="hidden text-[11px] font-medium text-muted-foreground lg:inline">对话</span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          selectableStyles.control,
          selectableStyles.standalone,
          "h-10 w-full justify-start rounded-xl px-3 text-sm font-medium hover:bg-transparent focus-visible:ring-2 focus-visible:ring-ring",
          isNewChatActive && "text-primary hover:text-primary"
        )}
        onClick={onNewChat}
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
        aria-pressed={isNewChatActive}
        title="新对话"
      >
        <span className={cn(glassStyles.lens, selectableStyles.lens)} aria-hidden="true" />
        <span className={cn(selectableStyles.content, "inline-flex items-center gap-2")}>
          <SquarePen className="h-4 w-4" />
          <span>新对话</span>
        </span>
      </Button>
    </div>
  );
};

export default ChatSidebarHeader;
