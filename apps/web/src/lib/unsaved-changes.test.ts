import { afterEach, describe, expect, it, vi } from "vitest";

import { confirmLeave, hasUnsavedChanges, LEAVE_PROMPT, leavesPage, setUnsavedChanges } from "./unsaved-changes";

const click = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
const anchor = (href: string, target = "", attrs: string[] = []) => ({ href, target, hasAttribute: (n: string) => attrs.includes(n) });
const here = { href: "https://app.example/config/strategy" };

describe("unsaved-changes registry", () => {
  const token = Symbol("editor");
  afterEach(() => setUnsavedChanges(token, false));

  it("lets navigation through without asking when nothing is dirty", () => {
    const ask = vi.fn(() => false);
    expect(hasUnsavedChanges()).toBe(false);
    expect(confirmLeave(ask)).toBe(true);
    expect(ask).not.toHaveBeenCalled();
  });

  it("asks while an editor is dirty and follows the answer", () => {
    setUnsavedChanges(token, true);
    const ask = vi.fn(() => false);
    expect(confirmLeave(ask)).toBe(false);
    expect(ask).toHaveBeenCalledWith(LEAVE_PROMPT);
    expect(confirmLeave(() => true)).toBe(true);
  });

  it("stops asking once the editor is clean again", () => {
    setUnsavedChanges(token, true);
    setUnsavedChanges(token, false);
    expect(hasUnsavedChanges()).toBe(false);
  });
});

describe("leavesPage", () => {
  it("guards a plain click on another in-app page", () => {
    expect(leavesPage(click, anchor("https://app.example/news"), here)).toBe(true);
    expect(leavesPage(click, anchor("/config/strategy?x=1"), here)).toBe(true);
  });

  it("ignores same-page hash links, new tabs, downloads and other origins", () => {
    expect(leavesPage(click, anchor("/config/strategy#limits"), here)).toBe(false);
    expect(leavesPage(click, anchor("/news", "_blank"), here)).toBe(false);
    expect(leavesPage(click, anchor("/export.json", "", ["download"]), here)).toBe(false);
    expect(leavesPage(click, anchor("https://elsewhere.example/"), here)).toBe(false);
  });

  it("ignores modified, non-primary and already-handled clicks", () => {
    expect(leavesPage({ ...click, metaKey: true }, anchor("/news"), here)).toBe(false);
    expect(leavesPage({ ...click, button: 1 }, anchor("/news"), here)).toBe(false);
    expect(leavesPage({ ...click, defaultPrevented: true }, anchor("/news"), here)).toBe(false);
  });
});
