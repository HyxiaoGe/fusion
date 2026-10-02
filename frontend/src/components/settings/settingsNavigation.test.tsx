import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { revealFocusedSettingsTab } from "./settingsNavigation";

let frames: FrameRequestCallback[];

beforeEach(() => {
  frames = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });
});

afterEach(() => vi.restoreAllMocks());

function flushFrames() {
  act(() => frames.splice(0).forEach((callback) => callback(0)));
}

function navigation() {
  const view = render(
    <div data-testid="navigation" onFocusCapture={revealFocusedSettingsTab}>
      <button role="tab">首项</button>
      <button role="tab">中间项</button>
      <button role="tab">末项</button>
      <input aria-label="其他控件" />
    </div>
  );
  const scroller = screen.getByTestId("navigation");
  const first = screen.getByRole("tab", { name: "首项" });
  const middle = screen.getByRole("tab", { name: "中间项" });
  const last = screen.getByRole("tab", { name: "末项" });
  Object.defineProperties(scroller, {
    clientWidth: { value: 300 },
    clientLeft: { value: 1 },
  });
  scroller.scrollTop = 40;
  vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 150, 302, 50));
  const scroll = vi.fn((options: ScrollToOptions) => { scroller.scrollLeft += options.left ?? 0; });
  Object.defineProperty(scroller, "scrollBy", { value: scroll });
  const position = (offset: number, width = 100) => new DOMRect(101 + offset - scroller.scrollLeft, 150, width, 40);
  vi.spyOn(first, "getBoundingClientRect").mockImplementation(() => position(0));
  vi.spyOn(middle, "getBoundingClientRect").mockImplementation(() => position(120));
  vi.spyOn(last, "getBoundingClientRect").mockImplementation(() => position(402, 120));
  const focus = (element: HTMLElement) => act(() => element.focus());
  const visible = (element: HTMLElement) => {
    const bounds = element.getBoundingClientRect();
    return bounds.left >= 101 && bounds.right <= 401;
  };
  return { ...view, scroller, first, middle, last, scroll, focus, visible, position };
}

describe("设置分类聚焦滚动", () => {
  it("末项越界时滚入视野，返回首项也会恢复可见且不改变纵向位置", () => {
    const nav = navigation();
    expect(nav.visible(nav.last)).toBe(false);
    nav.focus(nav.last);
    flushFrames();
    expect(nav.visible(nav.last)).toBe(true);
    expect(nav.scroller.scrollTop).toBe(40);

    expect(nav.visible(nav.first)).toBe(false);
    nav.focus(nav.first);
    flushFrames();
    expect(nav.visible(nav.first)).toBe(true);
    expect(nav.scroller.scrollLeft).toBe(0);
    expect(nav.scroller.scrollTop).toBe(40);
  });

  it("已完整可见的分类不会改变滚动位置", () => {
    const nav = navigation();
    nav.focus(nav.middle);
    flushFrames();
    expect(nav.scroll).not.toHaveBeenCalled();
  });

  it("其他控件聚焦不触发分类滚动", () => {
    const nav = navigation();
    nav.focus(screen.getByRole("textbox", { name: "其他控件" }));
    expect(frames).toHaveLength(0);
    expect(nav.scroll).not.toHaveBeenCalled();
  });

  it("连续切换只让最终焦点决定滚动，失效的旧焦点不能抢回位置", () => {
    const nav = navigation();
    nav.focus(nav.last);
    nav.focus(nav.first);
    flushFrames();
    expect(nav.visible(nav.first)).toBe(true);
    expect(nav.scroll).not.toHaveBeenCalled();
  });

  it("使用选中后更新的实际文字宽度，关闭导航后忽略待处理滚动", () => {
    const nav = navigation();
    nav.focus(nav.last);
    vi.mocked(nav.last.getBoundingClientRect).mockImplementation(() => nav.position(402, 140));
    flushFrames();
    expect(nav.visible(nav.last)).toBe(true);
    nav.focus(nav.first);
    const previous = nav.scroll.mock.calls.length;
    nav.unmount();
    flushFrames();
    expect(nav.scroll.mock.calls).toHaveLength(previous);
  });
});
