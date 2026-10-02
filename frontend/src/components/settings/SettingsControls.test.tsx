import { createRef, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectValue } from "@/components/ui/select";
import {
  SettingsButton, SettingsCheckbox, SettingsFieldError, SettingsInput,
  SettingsSelectContent, SettingsSelectItem, SettingsSelectTrigger,
  SettingsSwitch, SettingsTextarea,
} from "./SettingsControls";

beforeAll(() => {
  window.PointerEvent = MouseEvent as typeof PointerEvent;
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});

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

describe("设置表单控件的原生协议", () => {
  it("输入与多行编辑保留 ref、字符约束、表单名称和提交行为", async () => {
    const user = userEvent.setup();
    const submitted = vi.fn();
    const inputRef = createRef<HTMLInputElement>();
    const textareaRef = createRef<HTMLTextAreaElement>();
    render(<form aria-label="服务设置" onSubmit={(event) => { event.preventDefault(); submitted(); }}>
      <label htmlFor="service-name">服务名称</label>
      <SettingsInput id="service-name" ref={inputRef} name="service" required pattern="[a-z]+" maxLength={6} autoComplete="off" />
      <label htmlFor="service-notes">说明</label>
      <SettingsTextarea id="service-notes" ref={textareaRef} name="notes" maxLength={8} rows={3} />
      <SettingsButton type="submit">提交</SettingsButton>
    </form>);

    const input = screen.getByRole("textbox", { name: "服务名称" });
    const textarea = screen.getByRole("textbox", { name: "说明" });
    const form = screen.getByRole("form", { name: "服务设置" }) as HTMLFormElement;
    expect(inputRef.current).toBe(input);
    expect(textareaRef.current).toBe(textarea);
    expect(form.checkValidity()).toBe(false);
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(textarea).toHaveAttribute("rows", "3");
    await user.type(input, "fusionextra");
    await user.type(textarea, "第一行\n第二行超长内容");
    expect(input).toHaveValue("fusion");
    expect(textareaRef.current?.value.length).toBe(8);
    expect(form.checkValidity()).toBe(true);
    const values = new FormData(form);
    expect(values.get("service")).toBe("fusion");
    expect(values.get("notes")).toBe(textareaRef.current?.value);
    await user.click(screen.getByRole("button", { name: "提交" }));
    expect(submitted).toHaveBeenCalledTimes(1);
  });

  it("数字输入保留步进与上下限，禁用编辑不触发更改", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    const view = render(<SettingsInput aria-label="超时秒数" type="number" min={1} max={60} step={1} defaultValue={61} onChange={changed} />);
    const input = screen.getByRole("spinbutton", { name: "超时秒数" }) as HTMLInputElement;
    expect(input.validity.rangeOverflow).toBe(true);
    expect(input).toHaveAttribute("step", "1");
    view.rerender(<SettingsInput aria-label="超时秒数" type="number" min={1} max={60} step={1} defaultValue={61} disabled onChange={changed} />);
    await user.type(input, "5");
    expect(input).toHaveValue(61);
    expect(changed).not.toHaveBeenCalled();
  });

  it("错误组件保留 id/ref，并成为字段的可访问说明", () => {
    const errorRef = createRef<HTMLParagraphElement>();
    render(<>
      <label htmlFor="endpoint">服务地址</label>
      <SettingsInput id="endpoint" aria-invalid="true" aria-describedby="endpoint-hint endpoint-error" />
      <p id="endpoint-hint">使用 HTTPS 地址</p>
      <SettingsFieldError id="endpoint-error" ref={errorRef}>请填写有效的地址</SettingsFieldError>
    </>);
    const input = screen.getByRole("textbox", { name: "服务地址" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("使用 HTTPS 地址 请填写有效的地址");
    expect(errorRef.current).toBe(screen.getByRole("alert"));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写有效的地址");
  });

  it("下拉通过键盘选择，保留触发器/菜单/选项 ref、长标签和表单值", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    const triggerRef = createRef<HTMLButtonElement>();
    const contentRef = createRef<HTMLDivElement>();
    const itemRef = createRef<HTMLDivElement>();
    const longLabel = "用于复杂任务的完整模型标签，不因管理表单控件替换丢失文字内容";
    function Example() {
      const [value, setValue] = useState("first");
      return <form aria-label="模型设置">
        <Select name="model" value={value} onValueChange={(next) => { changed(next); setValue(next); }}>
          <SettingsSelectTrigger ref={triggerRef} aria-label="默认模型"><SelectValue /></SettingsSelectTrigger>
          <SettingsSelectContent ref={contentRef}>
            <SettingsSelectItem value="first">默认模型</SettingsSelectItem>
            <SettingsSelectItem value="disabled" disabled>不可用模型</SettingsSelectItem>
            <SettingsSelectItem ref={itemRef} value="last">{longLabel}</SettingsSelectItem>
          </SettingsSelectContent>
        </Select>
      </form>;
    }
    render(<Example />);
    const trigger = screen.getByRole("combobox", { name: "默认模型" });
    expect(triggerRef.current).toBe(trigger);
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    const content = await screen.findByRole("listbox");
    expect(contentRef.current).toBe(content);
    expect(itemRef.current).toBe(screen.getByRole("option", { name: longLabel }));
    expect(screen.getByRole("option", { name: "不可用模型" })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{End}{Enter}");
    expect(changed).toHaveBeenCalledExactlyOnceWith("last");
    expect(trigger).toHaveTextContent(longLabel);
    expect(trigger).toHaveFocus();
    expect(new FormData(screen.getByRole("form", { name: "模型设置" }) as HTMLFormElement).get("model")).toBe("last");
  });

  it("下拉 Escape 只关闭菜单并返回焦点，宿主 Dialog 保持打开", async () => {
    const user = userEvent.setup();
    const dialogChanged = vi.fn();
    const escaped = vi.fn();
    render(<Dialog defaultOpen onOpenChange={dialogChanged}>
      <DialogContent>
        <DialogTitle>配置服务</DialogTitle>
        <DialogDescription>选择服务配置</DialogDescription>
        <Select defaultValue="first">
          <SettingsSelectTrigger aria-label="服务类型"><SelectValue /></SettingsSelectTrigger>
          <SettingsSelectContent onEscapeKeyDown={escaped}>
            <SettingsSelectItem value="first">公开服务</SettingsSelectItem>
            <SettingsSelectItem value="second">私有服务</SettingsSelectItem>
          </SettingsSelectContent>
        </Select>
      </DialogContent>
    </Dialog>);
    const trigger = screen.getByRole("combobox", { name: "服务类型" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("listbox")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "配置服务" })).toBeVisible();
    expect(trigger).toHaveFocus();
    expect(escaped).toHaveBeenCalledTimes(1);
    expect(dialogChanged).not.toHaveBeenCalled();
  });

  it("原生受控复选框支持空格、表单值与禁用守卫", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    const ref = createRef<HTMLInputElement>();
    function Example() {
      const [checked, setChecked] = useState(false);
      return <form aria-label="启用设置"><label>
        <SettingsCheckbox ref={ref} name="enabled" value="yes" checked={checked} onChange={(event) => { changed(event.target.checked); setChecked(event.target.checked); }} />
        启用自动同步
      </label></form>;
    }
    const view = render(<Example />);
    const checkbox = screen.getByRole("checkbox", { name: "启用自动同步" });
    expect(ref.current).toBe(checkbox);
    await user.tab();
    await user.keyboard(" ");
    expect(checkbox).toBeChecked();
    expect(changed).toHaveBeenCalledExactlyOnceWith(true);
    expect(new FormData(screen.getByRole("form", { name: "启用设置" }) as HTMLFormElement).get("enabled")).toBe("yes");
    view.rerender(<SettingsCheckbox aria-label="启用自动同步" checked disabled onChange={changed} />);
    const disabled = screen.getByRole("checkbox", { name: "启用自动同步" });
    await user.click(disabled);
    disabled.focus();
    await user.keyboard(" ");
    expect(disabled).toBeChecked();
    expect(changed).toHaveBeenCalledTimes(1);
  });
});
