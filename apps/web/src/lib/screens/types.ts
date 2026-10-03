/** The screen builder's own types. Everything it shows is a figure the API returned. */

/** A metric the screen can filter on, and the endpoint that supplies it. */
export type MetricId =
  | "pe"
  | "pb"
  | "ps"
  | "peg"
  | "dividend_yield"
  | "gross_margin"
  | "operating_margin"
  | "net_margin"
  | "roe"
  | "roic"
  | "weight"
  | "ppl_pct"
  | "day_change_pct";

export type MetricSource = "valuation" | "holdings" | "prices";

export type MetricDef = {
  id: MetricId;
  label: string;
  /** "x" = a multiple shown as 18.2x; "pct" = the API sends a fraction (0.012), shown and entered as %. */
  unit: "x" | "pct";
  source: MetricSource;
  /** Where the figure comes from, for the table header and the provenance line. */
  endpoint: string;
  /** Staleness flags of the valuation response that make this figure stale. */
  staleWhen: readonly ("price" | "eps")[];
  heldOnly: boolean;
};

export type Op = "gte" | "lte";

/** One user criterion. `value` is the threshold as typed (display units); invalid text means "incomplete". */
export type Criterion = { metric: MetricId; op: Op; value: string };

/** What to do with an entity whose metric is unknown: it is never counted as passing or as zero. */
export type MissingTreatment = "exclude" | "unknown";

export type ScopeFilter = "all" | "held" | "watchlist";

export type UnknownReason = "missing" | "stale" | "not_covered" | "paid_only" | "unavailable";

export type Reading =
  | { status: "value"; value: number }
  | { status: "unknown"; reason: UnknownReason; detail: string; /** The stale figure, kept for display only. */ staleValue?: number };

export type Entity = {
  ticker: string;
  name: string | null;
  held: boolean;
  watchlist: boolean;
};

export type RatioFigure = { value: number | null; reason: string | null };

/** The result of `GET /holdings/{ticker}/valuation` for one entity, as the screen needs it. */
export type ValuationInput =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | {
      kind: "ok";
      ratios: Partial<Record<MetricId, RatioFigure>>;
      priceStale: boolean | null;
      epsStale: boolean | null;
      priceDate: string | null;
      checkedAt: string | null;
      sourceLabel: string | null;
      /** Last refresh error per source, as text. */
      errors: readonly string[];
      /** True when the response carries a price or any fundamentals at all. */
      hasAnyData: boolean;
    };

export type HeldInput = { ticker: string; name: string | null; weight: number | null; pplPct: number | null };
export type WatchInput = { ticker: string };
export type PriceInput = { ticker: string; dayChangePct: number | null; asOf: string | null };
/** A coverage-ledger entry in a gap state for a security entity. */
export type LedgerGap = { provider: string; dataset: string; rawState: string; reason: string | null };

export type ScreenInputs = {
  held: readonly HeldInput[];
  watchlist: readonly WatchInput[];
  prices: readonly PriceInput[];
  valuations: ReadonlyMap<string, ValuationInput>;
  /** Upper-cased ticker -> gap entries from GET /data/coverage. */
  ledger: ReadonlyMap<string, readonly LedgerGap[]>;
};

export type CriterionResult = { criterion: Criterion; reading: Reading; verdict: "pass" | "fail" | "unknown" };

export type OutcomeKind = "matched" | "failed" | "excluded" | "unknown" | "pending";

export type Outcome = {
  entity: Entity;
  kind: OutcomeKind;
  results: readonly CriterionResult[];
  /** Distinct reasons behind an excluded / unknown outcome. */
  reasons: readonly UnknownReason[];
};

export type ScreenSummary = {
  universe: number;
  matched: number;
  /** Evaluated and failed at least one criterion. */
  failed: number;
  /** Unknown on a criterion and dropped from the result (treatment "exclude"). */
  excluded: number;
  /** Unknown on a criterion and listed as unknown (treatment "unknown"). */
  unknown: number;
  pending: number;
  /** Entity counts per unknown reason, among excluded or unknown outcomes. */
  byReason: Record<UnknownReason, number>;
};
