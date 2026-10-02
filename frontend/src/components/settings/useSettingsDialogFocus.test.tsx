import { useRef, useState, type ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { SettingsDialogFocusProvider, useSettingsDialogOpener } from "./SettingsDialogFocusContext";
import { useSettingsDialogFocus } from "./useSettingsDialogFocus";

function Contents({ children }: { children?: ReactNode }) {
  return <><DialogTitle>编辑配置</DialogTitle><DialogDescription>填写配置后保存</DialogDescription>{children}</>;
}

function ControlledDialog({ restore = true, afterSave = "keep" }: { restore?: boolean; afterSave?: "keep" | "remove" | "disable" | "hide" | "css-hide" }) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const fallbackRef = useRef<HTMLHeadingElement>(null);
  const focus = useSettingsDialogFocus({ open, fallbackRef });
  return <section>
    <h2 ref={fallbackRef} tabIndex={-1}>管理区域</h2>
    {!(saved && afterSave === "remove") && <button
      hidden={saved && afterSave === "hide"}
      style={saved && afterSave === "css-hide" ? { display: "none" } : undefined}
      disabled={saved && afterSave === "disable"}
      onClick={(event) => { focus.captureOpener(event.currentTarget); setOpen(true); }}
    >打开编辑</button>}
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent {...(restore ? { onOpenAutoFocus: focus.onOpenAutoFocus, onCloseAutoFocus: focus.onCloseAutoFocus } : {})}>
        <Contents><input aria-label="名称" /><button onClick={() => { setSaved(true); setOpen(false); }}>保存编辑</button></Contents>
      </DialogContent>
    </Dialog>
  </section>;
}

function MenuDialog() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const fallbackRef = useRef<HTMLHeadingElement>(null);
  const openingDialogRef = useRef(false);
  const focus = useSettingsDialogFocus({ open, fallbackRef });
  return <>
    <h2 ref={fallbackRef} tabIndex={-1}>文档管理</h2>
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild><button ref={triggerRef}>文档操作</button></DropdownMenuTrigger>
      <DropdownMenuContent onCloseAutoFocus={(event) => {
        if (openingDialogRef.current) event.preventDefault();
        openingDialogRef.current = false;
      }}>
        <DropdownMenuItem onSelect={() => {
          openingDialogRef.current = true;
          focus.captureOpener(triggerRef.current);
          setOpen(true);
        }}>编辑文档</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent onOpenAutoFocus={focus.onOpenAutoFocus} onCloseAutoFocus={focus.onCloseAutoFocus}>
        <Contents><input aria-label="文档名称" /></Contents>
      </DialogContent>
    </Dialog>
  </>;
}

function NestedDialog() {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  const outerFallback = useRef<HTMLButtonElement>(null);
  const innerFallback = useRef<HTMLHeadingElement>(null);
  const outerFocus = useSettingsDialogFocus({ open: outerOpen, fallbackRef: outerFallback });
  const innerFocus = useSettingsDialogFocus({ open: innerOpen, fallbackRef: innerFallback });
  return <>
    <button ref={outerFallback} onClick={(event) => { outerFocus.captureOpener(event.currentTarget); setOuterOpen(true); }}>打开设置</button>
    <button onClick={() => { setOuterOpen(false); setInnerOpen(false); }}>关闭所有</button>
    <Dialog open={outerOpen} onOpenChange={setOuterOpen}>
      <DialogContent onOpenAutoFocus={outerFocus.onOpenAutoFocus} onCloseAutoFocus={outerFocus.onCloseAutoFocus}>
        <DialogTitle>设置</DialogTitle><DialogDescription>管理设置</DialogDescription>
        <h2 ref={innerFallback} tabIndex={-1}>知识库区域</h2>
        <button onClick={(event) => { innerFocus.captureOpener(event.currentTarget); setInnerOpen(true); }}>预览分块</button>
        <Dialog open={innerOpen} onOpenChange={setInnerOpen}>
          <DialogContent onOpenAutoFocus={innerFocus.onOpenAutoFocus} onCloseAutoFocus={innerFocus.onCloseAutoFocus}>
            <DialogTitle>分块预览</DialogTitle><DialogDescription>只读分块</DialogDescription><button>分块内容</button>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  </>;
}

describe("设置受控弹窗焦点归属", () => {
  it("基线：没有 DialogTrigger 的受控弹窗关闭后焦点落到 body", async () => {
    const user = userEvent.setup();
    render(<ControlledDialog restore={false} />);
    await user.click(screen.getByRole("button", { name: "打开编辑" }));
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(document.body).toHaveFocus());
  });

  it("保持默认初始输入焦点，键盘关闭后恢复真实入口", async () => {
    const user = userEvent.setup();
    render(<ControlledDialog />);
    await user.tab();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "打开编辑" })).toHaveFocus());
  });

  it.each(["remove", "disable", "hide", "css-hide"] as const)("保存后入口 %s 时回到仍有效的区域", async (afterSave) => {
    const user = userEvent.setup();
    render(<ControlledDialog afterSave={afterSave} />);
    await user.click(screen.getByRole("button", { name: "打开编辑" }));
    await user.click(screen.getByRole("button", { name: "保存编辑" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "管理区域" })).toHaveFocus());
  });

  it("dropdown 跳转不抢输入焦点，关闭后回真实菜单按钮", async () => {
    const user = userEvent.setup();
    render(<MenuDialog />);
    await user.tab();
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "文档名称" })).toHaveFocus());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "文档操作" })).toHaveFocus());
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "文档操作" })).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("关闭嵌套预览恢复父设置内入口并保留外层", async () => {
    const user = userEvent.setup();
    render(<NestedDialog />);
    await user.click(screen.getByRole("button", { name: "打开设置" }));
    await user.click(screen.getByRole("button", { name: "预览分块" }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "预览分块" })).toHaveFocus());
    expect(screen.getByRole("dialog", { name: "设置" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "打开设置" })).toHaveFocus());
  });

  it("同时关闭内外层时，子弹窗不抢主页面入口焦点", async () => {
    const user = userEvent.setup();
    render(<NestedDialog />);
    await user.click(screen.getByRole("button", { name: "打开设置" }));
    await user.click(screen.getByRole("button", { name: "预览分块" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭所有", hidden: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "打开设置" })).toHaveFocus());
  });

  it("父层退出动画期间仍连接的 closed DOM 不允许子层恢复", async () => {
    const user = userEvent.setup();
    function ExitingOwner() {
      const [open, setOpen] = useState(false);
      const [ownerClosed, setOwnerClosed] = useState(false);
      const fallbackRef = useRef<HTMLButtonElement>(null);
      const focus = useSettingsDialogFocus({ open, fallbackRef });
      return <>
        <button ref={fallbackRef}>主页面候选</button>
        <div role="dialog" aria-label="父设置" data-state={ownerClosed ? "closed" : "open"}>
          <button onClick={(event) => { focus.captureOpener(event.currentTarget); setOpen(true); }}>打开子层</button>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogContent onOpenAutoFocus={focus.onOpenAutoFocus} onCloseAutoFocus={focus.onCloseAutoFocus}>
              <Contents><button onClick={() => { setOwnerClosed(true); setOpen(false); }}>退出父设置</button></Contents>
            </DialogContent>
          </Dialog>
        </div>
      </>;
    }
    render(<ExitingOwner />);
    await user.click(screen.getByRole("button", { name: "打开子层" }));
    await user.click(screen.getByRole("button", { name: "退出父设置" }));
    expect(screen.getByRole("dialog", { name: "父设置" })).toHaveAttribute("data-state", "closed");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(document.body).toHaveFocus();
    expect(screen.getByRole("button", { name: "主页面候选" })).not.toHaveFocus();
  });

  it("快速重新打开后，旧关闭回调不夺走新输入焦点", async () => {
    const user = userEvent.setup();
    render(<ControlledDialog />);
    await user.click(screen.getByRole("button", { name: "打开编辑" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "打开编辑" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "名称" })).toHaveFocus());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveFocus();
  });

  it("整体卸载后，迟到的关闭回调不抢新页面焦点", async () => {
    const user = userEvent.setup();
    const view = render(<><button>新页面</button><ControlledDialog /></>);
    await user.click(screen.getByRole("button", { name: "打开编辑" }));
    view.rerender(<button>新页面</button>);
    const next = screen.getByRole("button", { name: "新页面" });
    next.focus();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(next).toHaveFocus();
  });

  it("退出期间用户已聚焦新控件时不覆盖焦点", async () => {
    const user = userEvent.setup();
    render(<><button>下一步</button><ControlledDialog /></>);
    await user.click(screen.getByRole("button", { name: "打开编辑" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    const next = screen.getByRole("button", { name: "下一步" });
    next.focus();
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(next).toHaveFocus();
  });

  it("provider 连接独立入口，未提供 provider 也可单独渲染", () => {
    const captured = vi.fn();
    function Entry() {
      const { captureOpener } = useSettingsDialogOpener();
      return <button onClick={(event) => captureOpener(event.currentTarget)}>头像</button>;
    }
    function Reader() {
      const { openerRef } = useSettingsDialogOpener();
      return <button onClick={() => captured(openerRef.current)}>读取入口</button>;
    }
    const view = render(<SettingsDialogFocusProvider><Entry /><Reader /></SettingsDialogFocusProvider>);
    fireEvent.click(screen.getByRole("button", { name: "头像" }));
    fireEvent.click(screen.getByRole("button", { name: "读取入口" }));
    expect(captured).toHaveBeenLastCalledWith(screen.getByRole("button", { name: "头像" }));
    view.rerender(<Entry />);
    fireEvent.click(screen.getByRole("button", { name: "头像" }));
  });
});
