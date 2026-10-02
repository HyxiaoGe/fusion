import { createRef, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { SettingsButton, SettingsSwitch } from "./SettingsControls";

describe("设置操作控件的原生协议", () => {
  it("键盘打开菜单并关闭后仍返回触发按钮", async () => {
    const user = userEvent.setup();
    render(<DropdownMenu>
      <DropdownMenuTrigger asChild><SettingsButton variant="outline" aria-label="文档操作">操作</SettingsButton></DropdownMenuTrigger>
      <DropdownMenuContent><DropdownMenuItem>编辑</DropdownMenuItem></DropdownMenuContent>
    </DropdownMenu>);
    await user.tab();
    expect(screen.getByRole("button", { name: "文档操作" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("menuitem", { name: "编辑" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "文档操作" })).toHaveFocus();
  });

  it("保留按钮 ref、表单提交、禁用与外部指针回调", async () => {
    const user = userEvent.setup();
    const submitted = vi.fn();
    const moved = vi.fn();
    const left = vi.fn();
    const ref = createRef<HTMLButtonElement>();
    const view = render(<form onSubmit={(event) => { event.preventDefault(); submitted(); }}>
      <SettingsButton type="submit" ref={ref} onPointerMove={moved} onPointerLeave={left}>保存</SettingsButton>
    </form>);
    const button = screen.getByRole("button", { name: "保存" });
    expect(ref.current).toBe(button);
    fireEvent.pointerMove(button);
    fireEvent.pointerLeave(button);
    expect(moved).toHaveBeenCalledTimes(1);
    expect(left).toHaveBeenCalledTimes(1);
    await user.tab();
    await user.keyboard("{Enter}");
    expect(submitted).toHaveBeenCalledTimes(1);
    view.rerender(<form onSubmit={(event) => { event.preventDefault(); submitted(); }}>
      <SettingsButton type="submit" disabled>保存</SettingsButton>
    </form>);
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(submitted).toHaveBeenCalledTimes(1);
  });

  it("asChild 保留单个链接及可访问名称", () => {
    render(<SettingsButton asChild variant="outline"><a href="#details">查看详情</a></SettingsButton>);
    expect(screen.getByRole("link", { name: "查看详情" })).toHaveAttribute("href", "#details");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("受控开关的空格操作与禁用守卫保持有效", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    function Example() {
      const [checked, setChecked] = useState(false);
      return <SettingsSwitch aria-label="启用服务" checked={checked} onCheckedChange={(value) => { changed(value); setChecked(value); }} />;
    }
    const view = render(<Example />);
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByRole("switch", { name: "启用服务" })).toBeChecked();
    expect(changed).toHaveBeenCalledTimes(1);
    expect(changed).toHaveBeenLastCalledWith(true);
    view.rerender(<SettingsSwitch aria-label="启用服务" checked disabled onCheckedChange={changed} />);
    await user.click(screen.getByRole("switch", { name: "启用服务" }));
    expect(changed).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("switch", { name: "启用服务" })).toBeChecked();
  });
});
