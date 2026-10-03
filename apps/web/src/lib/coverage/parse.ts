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

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function count(v: unknown): number | null {
  const n = toNumber(v);
  return n !== null && Number.isInteger(n) && n >= 0 ? n : null;
}

function isoOrNull(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/**
 * Narrows `GET /verification/coverage` (untyped in OpenAPI). One malformed row rejects the whole
 * body (null): a half-parsed table could pass for a complete one. `entities` null is kept as
 * "not applicable"; a missing count is never turned into zero.
 */
export function parseMeasuredCoverage(body: unknown): MeasuredCoverageRow[] | null {
  if (!Array.isArray(body)) return null;
  const rows: MeasuredCoverageRow[] = [];
  for (const r of body) {
    if (!isRecord(r) || typeof r.jurisdiction !== "string" || typeof r.domain !== "string" || typeof r.metric !== "string") return null;
    const observations = count(r.observations);
    const unknownValues = count(r.unknown_values);
    const sources = count(r.sources);
    const entities = r.entities === null || r.entities === undefined ? null : count(r.entities);
    if (observations === null || unknownValues === null || sources === null) return null;
    if (r.entities !== null && r.entities !== undefined && entities === null) return null;
    rows.push({
      jurisdiction: r.jurisdiction,
      domain: r.domain,
      metric: r.metric,
      observations,
      entities,
      unknownValues,
      sources,
      earliestPeriod: isoOrNull(r.earliest_period),
      latestPeriod: isoOrNull(r.latest_period),
      newestKnownAt: isoOrNull(r.newest_known_at),
    });
  }
  return rows;
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
