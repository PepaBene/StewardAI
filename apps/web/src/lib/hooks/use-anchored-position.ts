"use client";

import { useLayoutEffect, useState, type RefObject } from "react";

import { placePopover } from "@/lib/popover";

/**
 * `position: fixed` coordinates for a panel shown next to `anchor` while `open`, kept in
 * place on scroll and resize. Null until measured (render the panel hidden until then).
 */
export function useAnchoredPosition(
  open: boolean,
  anchor: RefObject<HTMLElement | null>,
  panel: RefObject<HTMLElement | null>,
): { top: number; left: number } | null {
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    // Measured before paint, so a position left from an earlier opening never shows.
    function measure() {
      const a = anchor.current?.getBoundingClientRect();
      const p = panel.current?.getBoundingClientRect();
      if (!a || !p) return;
      const { top, left } = placePopover(a, { width: p.width, height: p.height }, { width: window.innerWidth, height: window.innerHeight });
      setPosition((prev) => (prev && prev.top === top && prev.left === left ? prev : { top, left }));
    }
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (panel.current) observer?.observe(panel.current);
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, anchor, panel]);

  return open ? position : null;
}
