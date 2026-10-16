/*
 * Portfolio valuation view model (insight I5): groups the holdings the way the screen reads them
 * (overvalued, stale, undervalued or fair, not covered) and turns the stored history into the
 * stepped chart rows. Totals stay the backend's.
 */
import type { components } from "@/lib/api/schema";

import { num } from "./insight";

export type PortfolioFairValue = components["schemas"]["PortfolioFairValue"];
export type HoldingFairValue = components["schemas"]["HoldingFairValue"];
export type Basis = "dcf_default" | "dcf_base" | "multiple_base" | "thesis_target" | "consensus_target" | "peer_multiple";

export type ValuationGroups = { over: HoldingFairValue[]; stale: HoldingFairValue[]; under: HoldingFairValue[]; uncovered: HoldingFairValue[] };

/** Stale wins over the price comparison: an old fair value is not a verdict. */
export function groupHoldings(holdings: readonly HoldingFairValue[]): ValuationGroups {
  const out: ValuationGroups = { over: [], stale: [], under: [], uncovered: [] };
  for (const h of holdings) {
    const up = num(h.upside);
    if (num(h.fair_value) === null || up === null) out.uncovered.push(h);
    else if (h.stale) out.stale.push(h);
    else if (up < 0) out.over.push(h);
    else out.under.push(h);
  }
  const byUpside = (a: HoldingFairValue, b: HoldingFairValue) => (num(a.upside) ?? 0) - (num(b.upside) ?? 0);
  out.over.sort(byUpside);
  out.under.sort((a, b) => byUpside(b, a));
  return out;
}

/** Sum of fair values in the account currency over covered holdings (equals the backend total). */
export function coveredFairValue(holdings: readonly HoldingFairValue[]): number {
  return holdings.reduce((s, h) => s + (num(h.fair_value_account) ?? 0), 0);
}

/** History rows for the step chart: covered market value against the recorded fair value. */
export function historyRows(v: PortfolioFairValue) {
  return v.history.map((r) => ({ date: r.date, value: num(r.covered_value), fair: num(r.fair_value) }));
}
