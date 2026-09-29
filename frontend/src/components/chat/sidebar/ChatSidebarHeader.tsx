import React from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SquarePen } from "lucide-react";

interface ChatSidebarHeaderProps {
  onNewChat: () => void;
  isNewChatActive?: boolean;
}

const ChatSidebarHeader: React.FC<ChatSidebarHeaderProps> = ({ onNewChat, isNewChatActive = false }) => {
  return (
    <div className="flex flex-col gap-3 px-3 pb-3 pt-4">
      <div className="flex items-center justify-between px-2">
        <span className="text-base font-semibold tracking-tight text-foreground">Fusion AI</span>
        <span className="hidden text-[11px] font-medium text-muted-foreground lg:inline">对话</span>
      </div>
      <Button
        variant="outline"
        size="sm"
        className={cn(
          "h-10 w-full justify-start gap-2 rounded-xl border-border/80 bg-background px-3 text-sm font-medium shadow-fdv2-xs transition-colors hover:border-border-strong hover:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring",
          isNewChatActive && "border-primary/20 bg-primary/[0.07] text-primary hover:border-primary/30 hover:bg-primary/10 hover:text-primary"
        )}
        onClick={onNewChat}
        aria-pressed={isNewChatActive}
        title="新对话"
      >
        <SquarePen className="h-4 w-4" />
        <span>新对话</span>
      </Button>
    </div>
  );
};

export default ChatSidebarHeader;
