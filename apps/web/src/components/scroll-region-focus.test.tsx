// @vitest-environment jsdom
import { render, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HYDRATION_SETTLE_MS, ScrollRegionFocus, syncScrollRegions } from "./scroll-region-focus";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

function region(html: string, overflowing: boolean) {
  const host = document.createElement("div");
  host.innerHTML = `<div class="relative overflow-x-auto">${html}</div>`;
  const el = host.firstElementChild as HTMLElement;
  // jsdom has no layout: say what the browser would measure.
  Object.defineProperty(el, "scrollWidth", { configurable: true, value: overflowing ? 900 : 300 });
  Object.defineProperty(el, "clientWidth", { configurable: true, value: 300 });
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: 100 });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: 100 });
  return { host, el };
}

describe("syncScrollRegions", () => {
  it("makes an overflowing table wrapper keyboard-focusable", () => {
    const { host, el } = region("<table><tr><td>x</td></tr></table>", true);
    syncScrollRegions(host);
    expect(el.getAttribute("tabindex")).toBe("0");
  });

  it("leaves a wrapper that fits alone, and takes tabindex back when it stops overflowing", () => {
    const { host, el } = region("<table><tr><td>x</td></tr></table>", true);
    syncScrollRegions(host);
    Object.defineProperty(el, "scrollWidth", { configurable: true, value: 200 });
    syncScrollRegions(host);
    expect(el.hasAttribute("tabindex")).toBe(false);
    expect(region("<p>x</p>", false).el.hasAttribute("tabindex")).toBe(false);
  });

  it("does not add a tab stop where a link or button inside is already reachable, or touch an author's tabindex", () => {
    const withLink = region('<a href="/x">x</a>', true);
    syncScrollRegions(withLink.host);
    expect(withLink.el.hasAttribute("tabindex")).toBe(false);
    const own = region("<p>x</p>", true);
    own.el.setAttribute("tabindex", "-1");
    syncScrollRegions(own.host);
    expect(own.el.getAttribute("tabindex")).toBe("-1");
  });
});

describe("ScrollRegionFocus", () => {
  it("waits for hydration to settle before touching server-rendered markup (a mismatch warning otherwise)", () => {
    vi.useFakeTimers();
    const { host, el } = region("<table><tr><td>x</td></tr></table>", true);
    document.body.append(host);
    render(<ScrollRegionFocus />);
    vi.advanceTimersByTime(HYDRATION_SETTLE_MS - 100);
    expect(el.hasAttribute("tabindex")).toBe(false);
    vi.advanceTimersByTime(200);
    expect(el.getAttribute("tabindex")).toBe("0");
  });
});
