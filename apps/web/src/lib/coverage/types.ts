import type { DataState } from "@/lib/data-state/states";
import type { EntityKind } from "@/lib/source-coverage/types";

/** The atlas columns. The API's entity_kind has exactly these three values. */
export const ATLAS_KINDS = ["security", "geography", "other"] as const satisfies readonly EntityKind[];

/** Dimensions `GET /data/coverage/summary` can group by (backend `group_by`). */
export const SUMMARY_GROUPS = ["metric", "source", "period", "state", "security", "geography"] as const;
export type SummaryGroupBy = (typeof SUMMARY_GROUPS)[number];

/** Atlas filters; every one is applied by the backend, none client-side. */
export type AtlasFilters = {
  provider: string;
  dataset: string;
  /** `any` = no entity-kind filter. */
  kind: EntityKind | "any";
  /** Backend ledger state string (available, paid_only, ...), or "". */
  state: string;
  /** ISO date; entries whose span contains it. */
  period: string;
  /** Exact entity key. */
  entity: string;
  group: SummaryGroupBy;
};

/** The drilled matrix cell: one dataset in one entity kind. */
export type AtlasDrill = { dataset: string; kind: EntityKind } | null;

export type AtlasCell = {
  dataset: string;
  kind: EntityKind;
  /** Ledger entries in the cell (count of API rows). 0 = no entry: unknown, never zero. */
  entries: number;
  /** Entries whose backend state is "available" (verified or partial). */
  available: number;
  byState: Partial<Record<DataState, number>>;
  /** Headline state, null when the cell has no ledger entry. */
  state: DataState | null;
};

export type AtlasRow = {
  dataset: string;
  providers: readonly string[];
  cells: Record<EntityKind, AtlasCell>;
};

/** One group of `GET /data/coverage/summary`. */
export type SummaryGroup = {
  key: string | null;
  entries: number;
  /** Backend state string -> entries. */
  byState: Readonly<Record<string, number>>;
  expectedPeriods: number | null;
  availablePeriods: number | null;
  entriesWithExpected: number;
  availableEntries: number;
  coverageRatio: number | null;
};

export type CoverageSummaryData = {
  groupBy: SummaryGroupBy;
  totalEntries: number;
  groups: readonly SummaryGroup[];
  note: string;
};

/** A row of `GET /verification/coverage` (measured coverage by jurisdiction and metric). */
export type MeasuredCoverageRow = {
  jurisdiction: string;
  domain: string;
  metric: string;
  observations: number;
  /** null = not applicable (macro series have no entity), never a measured zero. */
  entities: number | null;
  unknownValues: number;
  sources: number;
  earliestPeriod: string | null;
  latestPeriod: string | null;
  newestKnownAt: string | null;
};

/**
 * met / not_met: a measured figure against the plan's threshold. reported: a measured figure
 * whose target is qualitative (shown, not judged). unavailable: the endpoint failed or has no
 * data. not_measured: no API endpoint returns this figure.
 */
export type ScoreStatus = "met" | "not_met" | "reported" | "unavailable" | "not_measured";

export type ScoreItem = {
  id: string;
  title: string;
  /** The plan's release target, as text. */
  target: string;
  status: ScoreStatus;
  /** The measured figure with its denominator, or null. */
  measured: string | null;
  /** Share 0..1 for the meter, when the API gave a numerator and denominator. */
  share: number | null;
  detail: string;
  /** Endpoint(s) the figure came from, or the missing endpoint. */
  source: string;
  asOf: string | null;
};
