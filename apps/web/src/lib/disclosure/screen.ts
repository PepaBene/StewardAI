import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type ScreenResult = S["AnalyticsScreenOut"];
export type ScreenExcluded = S["AnalyticsScreenExcludedOut"];
export type LookThrough = S["AnalyticsLookThroughOut"];

export type CriterionKey = "min_cash_conversion" | "min_debt_maturity_coverage" | "min_discretionary_buys" | "min_stake_change_pct" | "min_customer_concentration" | "min_segment_surprise_pct" | "deal_exposure";

export type CriterionDef = { key: CriterionKey; field: string; label: string; hint: string; kind: "number" | "count" | "flag"; abs: boolean };

export const CRITERIA: readonly CriterionDef[] = [
  { key: "min_cash_conversion", field: "cash_conversion", label: "Cash conversion at least", hint: "Operating cash flow divided by net income; a loss year has no value.", kind: "number", abs: false },
  { key: "min_debt_maturity_coverage", field: "debt_maturity_coverage", label: "Cash covers debt due in year 1, at least (times)", hint: "From the debt note's maturity table.", kind: "number", abs: false },
  { key: "min_discretionary_buys", field: "discretionary_buys", label: "Discretionary insider buys, at least (count)", hint: "Open-market purchases not under a trading plan, in the window below.", kind: "count", abs: false },
  { key: "min_stake_change_pct", field: "stake_change_pct", label: "Latest stake change, at least (points, either way)", hint: "Latest 13D/13G against the filing it names as prior.", kind: "number", abs: true },
  { key: "min_customer_concentration", field: "customer_concentration", label: "Largest confirmed customer, at least (0 to 1)", hint: "Only confirmed relationships with a stated share.", kind: "number", abs: false },
  { key: "min_segment_surprise_pct", field: "segment_surprise_pct", label: "Segment surprise, at least (fraction, either way)", hint: "Latest segment actual against the estimate known before release.", kind: "number", abs: true },
  { key: "deal_exposure", field: "deal_exposure", label: "Has an active deal", hint: "A holding with no deal known is excluded as unknown, not as free of deals.", kind: "flag", abs: false },
];

export type FormValues = Partial<Record<CriterionKey, string | boolean>> & { insider_window_days?: string };

export type ParsedQuery = { query: Record<string, string | number | boolean>; errors: string[] };

/** Turns the form into API query values. A blank box is not a criterion; a bad number is an error, never a zero. */
export function parseForm(values: FormValues): ParsedQuery {
  const query: Record<string, string | number | boolean> = {};
  const errors: string[] = [];
  for (const c of CRITERIA) {
    const raw = values[c.key];
    if (c.kind === "flag") {
      if (raw === true) query[c.key] = true;
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (text === "") continue;
    const n = Number(text);
    if (!Number.isFinite(n)) errors.push(`${c.label}: "${text}" is not a number.`);
    else if (n < 0 && c.key !== "min_cash_conversion") errors.push(`${c.label}: must not be negative.`);
    else if (c.kind === "count" && (!Number.isInteger(n) || n < 1)) errors.push(`${c.label}: use a whole number of 1 or more.`);
    else if (c.key === "min_customer_concentration" && n > 1) errors.push(`${c.label}: use a fraction between 0 and 1.`);
    else query[c.key] = c.kind === "count" ? n : text;
  }
  const win = (values.insider_window_days ?? "").trim();
  if (win !== "") {
    const n = Number(win);
    if (!Number.isInteger(n) || n < 1 || n > 365) errors.push("Insider window: use 1 to 365 days.");
    else query.insider_window_days = n;
  }
  return { query, errors };
}

export const activeCount = (query: Record<string, unknown>): number => CRITERIA.filter((c) => query[c.key] !== undefined).length;

export type SampleHolding = { ticker: string; value: number; fields: Partial<Record<string, { value: number | boolean; period: string | null; source: string }>> };

/** The same rules as the backend, for labelled sample data only: missing is excluded, failing is excluded, nothing scores zero. */
export function runScreen(universe: readonly SampleHolding[], query: Record<string, string | number | boolean>): { matched: string[]; missing: string[]; failed: string[]; universe: number; active: number } {
  const active = CRITERIA.filter((c) => query[c.key] !== undefined);
  const matched: string[] = [];
  const missing: string[] = [];
  const failed: string[] = [];
  if (active.length === 0) return { matched, missing, failed, universe: universe.length, active: 0 };
  for (const h of universe) {
    let anyMissing = false;
    let anyFailed = false;
    for (const c of active) {
      const f = h.fields[c.field];
      if (!f) {
        anyMissing = true;
        continue;
      }
      if (c.kind === "flag") {
        if (f.value !== true) anyFailed = true;
        continue;
      }
      const v = Number(f.value);
      if ((c.abs ? Math.abs(v) : v) < Number(query[c.key])) anyFailed = true;
    }
    if (anyMissing) missing.push(h.ticker);
    else if (anyFailed) failed.push(h.ticker);
    else matched.push(h.ticker);
  }
  matched.sort();
  return { matched, missing: missing.sort(), failed: failed.sort(), universe: universe.length, active: active.length };
}

/** One honest sentence: matched against the whole universe, with both exclusion kinds counted. */
export function summary(r: Pick<ScreenResult, "universe_size" | "matched_count" | "excluded_missing" | "excluded_failed" | "active_criteria">): string {
  if (r.active_criteria.length === 0) return `Universe of ${r.universe_size} holdings. Choose a criterion to screen them.`;
  return `${r.matched_count} of ${r.universe_size} holdings matched; ${r.excluded_missing} excluded for missing data, ${r.excluded_failed} excluded for failing a bar.`;
}

export const weightPct = (w: string | null | undefined, digits = 1): string => (w === null || w === undefined || !Number.isFinite(Number(w)) ? "unknown" : `${(Number(w) * 100).toFixed(digits)}%`);

export function criterionLabel(key: string): string {
  return CRITERIA.find((c) => c.key === key)?.label ?? key.replaceAll("_", " ");
}

export type LookRow = { label: string; weight: string | null; holdings: string[]; note: string };

/** Totals rows for the look-through panel; every weight is a share of the whole portfolio, shown with its holdings. */
export function lookRows(lt: LookThrough): LookRow[] {
  const t = lt.totals;
  return [
    { label: "Holdings with a confirmed relationship", weight: t.relationship_covered_weight, holdings: [], note: `${t.relationship_covered_holdings} holdings` },
    { label: "Relationship share unknown", weight: t.unknown_weight, holdings: t.unknown_weight_holdings, note: "Stated nowhere: not included in any weighted total." },
    { label: "Holdings with a concentrated customer", weight: t.concentrated_customer_weight, holdings: t.concentrated_customer_holdings, note: `A customer at ${weightPct(lt.concentration_share, 0)} of revenue or more.` },
    { label: "Holdings with an active deal", weight: t.deal_exposed_weight, holdings: t.deal_exposed_holdings, note: "Deals known at the date." },
    { label: "Reached through held funds", weight: t.fund_look_through_weight, holdings: [], note: lt.max_fund_lag_days === null ? "No fund holdings read." : `Funds report up to ${lt.max_fund_lag_days} days behind the date.` },
    { label: "Fund lines not matched to a company", weight: t.unmapped_fund_weight, holdings: [], note: "Counted, not guessed." },
  ];
}
