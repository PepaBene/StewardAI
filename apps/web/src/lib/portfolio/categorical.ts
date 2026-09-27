/** Ledger's 8-hue categorical palette, fixed order, never cycled within one chart. */
export const CATEGORICAL_SLOTS = [
  "bg-categorical-1",
  "bg-categorical-2",
  "bg-categorical-3",
  "bg-categorical-4",
  "bg-categorical-5",
  "bg-categorical-6",
  "bg-categorical-7",
  "bg-categorical-8",
] as const;

/**
 * One categorical hue class per item, in first-seen order, held fixed for the life of the
 * list; a muted item (cash, unclassified — identity coding doesn't apply) gets the neutral
 * class instead. Shared by every chart drawing the same allocation, e.g. a bar-list and a
 * single-row stacked bar of the SAME items, so their colours always agree.
 */
export function categoricalClasses(items: readonly { muted?: boolean }[]): string[] {
  let next = 0;
  return items.map((item) => (item.muted ? "bg-neutral-signal" : CATEGORICAL_SLOTS[next++ % CATEGORICAL_SLOTS.length]));
}

/** The same fixed hue order as CSS var() strings, for an SVG chart's `fill` (Recharts'
 *  `Cell` takes a colour, not a class) — the donut must land on the exact same hue per item
 *  as the bar-list and stacked bar drawing the same allocation, never a second colour just
 *  because it's drawn a different way. */
const CATEGORICAL_VARS = [
  "var(--categorical-1)",
  "var(--categorical-2)",
  "var(--categorical-3)",
  "var(--categorical-4)",
  "var(--categorical-5)",
  "var(--categorical-6)",
  "var(--categorical-7)",
  "var(--categorical-8)",
] as const;

export function categoricalFills(items: readonly { muted?: boolean }[]): string[] {
  let next = 0;
  return items.map((item) => (item.muted ? "var(--neutral-signal)" : CATEGORICAL_VARS[next++ % CATEGORICAL_VARS.length]));
}
