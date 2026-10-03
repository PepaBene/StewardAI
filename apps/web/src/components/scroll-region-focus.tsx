"use client";

import { useEffect } from "react";

const MARK = "data-scroll-focus";
const CANDIDATES = ".overflow-x-auto, .overflow-y-auto, .overflow-auto";
const FOCUSABLE = "a[href], button, input, select, textarea, summary, [tabindex]";

function scrolls(el: HTMLElement): boolean {
  return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
}

/**
 * A region that scrolls must be reachable by keyboard (WCAG 2.1.1; axe `scrollable-region-focusable`):
 * a keyboard user cannot scroll a table that has no focusable content. Whether a table wrapper
 * scrolls depends on the data and the viewport, so this gives `tabindex="0"` to exactly the
 * wrappers that overflow right now and takes it back when they stop. A region that already
 * holds something focusable (a link, a button) is left alone: Tab reaches it there.
 */
export function syncScrollRegions(root: ParentNode): void {
  for (const el of root.querySelectorAll<HTMLElement>(CANDIDATES)) {
    const marked = el.hasAttribute(MARK);
    if (!marked && el.hasAttribute("tabindex")) continue;
    const needs = scrolls(el) && !el.querySelector(FOCUSABLE);
    if (needs && !marked) {
      el.setAttribute("tabindex", "0");
      el.setAttribute(MARK, "");
    } else if (!needs && marked) {
      el.removeAttribute("tabindex");
      el.removeAttribute(MARK);
    }
  }
}

/** How long after the page has loaded before the first pass: React must have hydrated the page. */
export const HYDRATION_SETTLE_MS = 1500;
const DEBOUNCE_MS = 250;

/**
 * Mounted once in the root layout; renders nothing. The first pass waits for the load event and a
 * settle delay: setting an attribute on server-rendered markup that React has not hydrated yet
 * is reported as a hydration mismatch.
 */
export function ScrollRegionFocus() {
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ready = false;
    const schedule = () => {
      if (!ready) return;
      clearTimeout(timer);
      timer = setTimeout(() => syncScrollRegions(document), DEBOUNCE_MS);
    };
    const start = () => {
      timer = setTimeout(() => {
        ready = true;
        syncScrollRegions(document);
      }, HYDRATION_SETTLE_MS);
    };
    if (document.readyState === "complete") start();
    else window.addEventListener("load", start, { once: true });
    const mutations = new MutationObserver(schedule);
    mutations.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    return () => {
      clearTimeout(timer);
      mutations.disconnect();
      window.removeEventListener("load", start);
      window.removeEventListener("resize", schedule);
    };
  }, []);
  return null;
}
