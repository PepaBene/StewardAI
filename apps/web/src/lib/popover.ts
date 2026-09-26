/*
 * Where to put a floating panel (citation preview, feedback form) next to the element that
 * opened it, in viewport coordinates for `position: fixed`: below it when it fits, else
 * above; its left edge aligned with the anchor but kept inside the viewport margins. Fixed
 * positioning keeps the panel out of any scrolling table's clipping.
 */

export type Rect = { top: number; bottom: number; left: number; right: number };
export type Size = { width: number; height: number };

export function placePopover(
  anchor: Rect,
  panel: Size,
  viewport: Size,
  { gap = 4, margin = 8 }: { gap?: number; margin?: number } = {},
): { top: number; left: number; side: "below" | "above" } {
  const spaceBelow = viewport.height - anchor.bottom - gap - margin;
  const spaceAbove = anchor.top - gap - margin;
  const side = panel.height <= spaceBelow || spaceBelow >= spaceAbove ? "below" : "above";
  const rawTop = side === "below" ? anchor.bottom + gap : anchor.top - gap - panel.height;
  const top = Math.max(margin, Math.min(rawTop, viewport.height - margin - panel.height));
  const maxLeft = viewport.width - margin - panel.width;
  const left = Math.max(margin, Math.min(anchor.left, maxLeft));
  return { top: Math.round(top), left: Math.round(left), side };
}
