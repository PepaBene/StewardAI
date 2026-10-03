import type { components } from "@/lib/api/schema";
import { toDataState } from "@/lib/data-state/states";
import { toNumber } from "@/lib/source-coverage/parse";

import { SUMMARY_GROUPS, type CoverageSummaryData, type MeasuredCoverageRow, type SummaryGroup } from "./types";

type Schemas = components["schemas"];

export { toDataState, toNumber };

export function adaptSummary(body: Schemas["CoverageSummary"]): CoverageSummaryData {
  const groupBy = SUMMARY_GROUPS.find((g) => g === body.group_by) ?? "metric";
  return {
    groupBy,
    totalEntries: body.total_entries,
    note: body.note,
    groups: body.groups.map(
      (g): SummaryGroup => ({
        key: g.key ?? null,
        entries: g.entries,
        byState: g.by_state,
        expectedPeriods: toNumber(g.expected_periods),
        availablePeriods: toNumber(g.available_periods),
        entriesWithExpected: g.entries_with_expected,
        availableEntries: g.available_entries,
        coverageRatio: toNumber(g.coverage_ratio),
      }),
    ),
  };
}

/**
 * `GET /verification/coverage` (typed in OpenAPI since the third backend wave) -> rows. `entities`
 * null stays "not applicable" (macro series have no entity); nothing is turned into zero.
 */
export function adaptMeasuredCoverage(rows: readonly Schemas["MeasuredCoverageRow"][]): MeasuredCoverageRow[] {
  return rows.map((r) => ({
    jurisdiction: r.jurisdiction,
    domain: r.domain,
    metric: r.metric,
    observations: r.observations,
    entities: r.entities,
    unknownValues: r.unknown_values,
    sources: r.sources,
    earliestPeriod: r.earliest_period || null,
    latestPeriod: r.latest_period || null,
    newestKnownAt: r.newest_known_at || null,
  }));
}

/** A ratio shown with its numerator and denominator (backend `Share`). */
export type ShareFigure = { numerator: number; denominator: number; ratio: number | null; basis: string };

export function adaptShare(s: Schemas["Share"] | null | undefined): ShareFigure | null {
  if (!s) return null;
  const numerator = toNumber(s.numerator);
  const denominator = toNumber(s.denominator);
  if (numerator === null || denominator === null) return null;
  return { numerator, denominator, ratio: toNumber(s.ratio), basis: s.denominator_basis };
}

export type DataContextFigures = {
  snapshotDate: string | null;
  knownAsOf: string;
  baseCurrency: string | null;
  positionCount: number;
  identity: ShareFigure | null;
  returnBases: readonly { basis: string; complete: ShareFigure | null; missingTickers: readonly string[]; partialTickers: readonly string[] }[];
  gaps: readonly { domain: string; state: string; reason: string }[];
};

export function adaptDataContext(d: Schemas["PortfolioDataContext"]): DataContextFigures {
  return {
    snapshotDate: d.snapshot_date ?? null,
    knownAsOf: d.known_as_of,
    baseCurrency: d.base_currency ?? null,
    positionCount: d.position_count,
    identity: adaptShare(d.identity_coverage),
    returnBases: (d.adjusted_return_coverage?.bases ?? []).map((b) => ({
      basis: b.adjustment_basis,
      complete: adaptShare(b.complete),
      missingTickers: b.missing_tickers,
      partialTickers: b.partial_tickers,
    })),
    gaps: d.gaps.map((g) => ({ domain: g.domain, state: g.state, reason: g.reason })),
  };
}
