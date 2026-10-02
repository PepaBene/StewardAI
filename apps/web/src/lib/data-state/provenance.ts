import { isFiniteNumber } from "@/lib/charts/scale";

import type { DataState } from "./states";

/**
 * What every view that consumes new data shows beside it: source, as-of date, method,
 * access state and the coverage denominator (Phase 0 frontend contract).
 */
export type DataCoverage = {
  covered: number;
  total: number;
  /** What is counted: "holdings", "countries", "listings", "peers". */
  unit: string;
};

export type DataProvenance = {
  /** Provider and dataset, e.g. "World Bank · GDP growth (NY.GDP.MKTP.KD.ZG)". */
  source: string;
  /** Link to the source page or stored evidence document. */
  sourceUrl?: string | null;
  /** The date the observation describes (not the retrieval time). */
  asOf: string | null;
  /** When the app learned of it; point-in-time queries use this, not asOf. */
  knownAt?: string | null;
  /** Method / transformation version, e.g. "indexed to 100 at first period". */
  method?: string | null;
  state: DataState;
  /** Human-readable reason for any non-verified state. */
  reason?: string | null;
  coverage?: DataCoverage | null;
};

/** covered / total as a fraction, or null when the denominator is missing or zero. */
export function coverageShare(c: DataCoverage | null | undefined): number | null {
  if (!c || !isFiniteNumber(c.covered) || !isFiniteNumber(c.total) || c.total <= 0) return null;
  return Math.min(1, Math.max(0, c.covered / c.total));
}

/** "18 of 24 holdings (75%)", or "No denominator" — never an unqualified percentage. */
export function describeCoverage(c: DataCoverage | null | undefined): string {
  const share = coverageShare(c);
  if (!c || share === null) return "No coverage denominator";
  return `${c.covered} of ${c.total} ${c.unit} (${Math.round(share * 100)}%)`;
}

/**
 * The state a view should wear once its coverage is known: a verified dataset whose coverage
 * is below 100% is "partial", never "verified". Other states pass through unchanged.
 */
export function stateWithCoverage(state: DataState, c: DataCoverage | null | undefined): DataState {
  if (state !== "verified") return state;
  const share = coverageShare(c);
  return share !== null && share < 1 ? "partial" : state;
}
