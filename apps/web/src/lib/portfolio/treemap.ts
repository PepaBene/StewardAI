import type { Holding } from "@/lib/api/client";

import { displayTicker } from "./holdings-view";

/*
 * The Holdings treemap: tile area is the position's weight, tile colour one of two moves on
 * the diverging heat scale — today's close-to-close move, or the unrealised P/L since
 * purchase (the earlier "value by P/L" map, kept as a toggle).
 */

export type TreemapMode = "day" | "ppl";

export const TREEMAP_MODES: Record<
  TreemapMode,
  { label: string; kicker: string; title: string; cap: number; description: string }
> = {
  day: {
    label: "Day move",
    kicker: "Today",
    title: "Daily move, sized by weight",
    cap: 0.03,
    description: "latest close against the previous close",
  },
  ppl: {
    label: "Unrealised P/L",
    kicker: "Since purchase",
    title: "Unrealised P/L, sized by weight",
    cap: 0.2,
    description: "unrealised P/L as a share of cost",
  },
};

export type TreemapInput = {
  id: string;
  label: string;
  weight: number;
  change: number | null;
  href: string;
};

/**
 * One tile per position with a positive value. Size is the position's weight, or its share
 * of the total value when it has no weight; colour is the chosen move (null -> neutral tile).
 */
export function treemapItems(
  holdings: readonly Pick<Holding, "ticker" | "value_base" | "weight" | "ppl_pct">[],
  mode: TreemapMode,
  dayChange: (ticker: string) => number | null | undefined,
): TreemapInput[] {
  const valued = holdings.filter((h) => h.value_base > 0);
  const total = valued.reduce((s, h) => s + h.value_base, 0);
  return valued.map((h) => {
    const change = mode === "day" ? dayChange(h.ticker) : h.ppl_pct;
    return {
      id: h.ticker,
      label: displayTicker(h.ticker),
      weight: h.weight ?? h.value_base / total,
      change: typeof change === "number" && Number.isFinite(change) ? change : null,
      href: `/companies/${encodeURIComponent(h.ticker)}`,
    };
  });
}
