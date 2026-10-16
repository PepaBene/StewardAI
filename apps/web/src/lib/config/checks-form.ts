/*
 * The Checks config (insight plan I3.10) as a form: every limit the checklist, the dividend tiers
 * and the fair-value staleness rules read. One spec row per field drives the inputs, the client
 * bounds and the body conversion; the backend validates again on save.
 */
import type { components } from "@/lib/api/schema";

export type ChecksConfig = components["schemas"]["ChecksConfig"];
type Group = "limits" | "dividends" | "fair_value";

/** percent: stored as a fraction, shown as %. ratio: a plain number ("1.5x"). */
export type FieldKind = "percent" | "number" | "int" | "money" | "bool";

export type FieldSpec = {
  group: Group;
  key: string;
  label: string;
  kind: FieldKind;
  min?: number;
  max?: number;
  /** Strict lower bound (exclusive). */
  above?: number;
  hint: string;
  section: string;
};

const L = (key: string, label: string, kind: FieldKind, section: string, hint: string, bounds: Partial<Pick<FieldSpec, "min" | "max" | "above">> = {}): FieldSpec => ({
  group: "limits",
  key,
  label,
  kind,
  section,
  hint,
  ...bounds,
});

export const SECTIONS = [
  { id: "value", title: "Value", note: "Price against fair value, peers and growth." },
  { id: "future", title: "Future", note: "Checks that need analyst estimates." },
  { id: "past", title: "Past", note: "Returns on capital and earnings quality." },
  { id: "health", title: "Health", note: "Debt, liquidity and interest cover." },
  { id: "dividend", title: "Dividend", note: "Payout, record and yield." },
  { id: "risk", title: "Risk checks", note: "The separate list of warning signs." },
  { id: "income", title: "Income forecast", note: "How suggested dividends are confirmed and estimated." },
  { id: "fair_value", title: "Fair-value alerts", note: "When a holding's fair value is stale or has moved." },
] as const;

export const FIELDS: readonly FieldSpec[] = [
  L("peg_max", "Highest PEG that passes", "number", "value", "Price/earnings over growth; 1.5 by default.", { above: 0, max: 10 }),
  L("fcf_yield_margin", "Free cash flow yield above the risk-free rate by", "percent", "value", "-10 % to 20 %.", { min: -0.1, max: 0.2 }),
  L("forecast_roe_floor", "Forecast ROE floor", "percent", "future", "-100 % to 200 %.", { min: -1, max: 2 }),
  L("analyst_count_min", "Fewest analysts for a usable consensus", "int", "future", "1–100.", { min: 1, max: 100 }),
  L("roe_floor", "Return on equity floor", "percent", "past", "15 % by default.", { min: -1, max: 2 }),
  L("roce_floor", "Return on capital employed floor", "percent", "past", "15 % by default.", { min: -1, max: 2 }),
  L("accruals_max", "Highest accruals ratio", "percent", "past", "Also used by the high-accruals risk check.", { min: 0, max: 1 }),
  L("debt_to_equity_max", "Highest debt to equity", "percent", "health", "40 % by default.", { min: 0, max: 20 }),
  L("net_debt_to_ebitda_max", "Highest net debt to EBITDA", "number", "health", "Times; 0–20.", { min: 0, max: 20 }),
  L("cfo_to_debt_min", "Operating cash flow covers at least this share of debt", "percent", "health", "20 % by default.", { min: 0, max: 5 }),
  L("interest_coverage_min", "Lowest interest coverage", "number", "health", "Times EBIT over interest; net interest earners are not applicable.", { min: 0, max: 100 }),
  L("payout_limit_earnings", "Highest payout of earnings", "percent", "dividend", "75 % by default.", { above: 0, max: 2 }),
  L("payout_limit_cash", "Highest payout of free cash flow", "percent", "dividend", "75 % by default.", { above: 0, max: 2 }),
  L("dividend_yield_floor", "Yield below which peer comparison is not meaningful", "percent", "dividend", "0–20 %.", { min: 0, max: 0.2 }),
  L("dividend_growth_years", "Years of dividend growth measured", "int", "dividend", "3–20.", { min: 3, max: 20 }),
  L("no_cut_years_min", "Years without a cut that pass", "int", "dividend", "1–30.", { min: 1, max: 30 }),
  L("dilution_max", "Highest share dilution a year", "percent", "risk", "5 % by default.", { min: 0, max: 1 }),
  L("small_cap_max", "Small company below (market cap, USD)", "money", "risk", "2,000,000,000 by default.", { min: 0 }),
  L("liquidity_floor", "Lowest average daily turnover (USD)", "money", "risk", "Below it the listing counts as illiquid.", { min: 0 }),
  L("volatility_vs_benchmark_max", "Highest volatility against the benchmark", "number", "risk", "Times the benchmark's.", { above: 0, max: 10 }),
  L("benchmark_volatility", "Benchmark volatility a year", "percent", "risk", "16 % by default.", { above: 0, max: 2 }),
  L("debt_maturity_share_max", "Highest share of debt due within a year", "percent", "risk", "0–100 %.", { min: 0, max: 1 }),
  L("earnings_window_days", "Earnings date flagged within (days)", "int", "risk", "1–90. Informational, never scored.", { min: 1, max: 90 }),
  L("insider_window_days", "Insider selling window (days)", "int", "risk", "7–365.", { min: 7, max: 365 }),
  { group: "dividends", key: "auto_confirm", label: "Confirm suggested dividends that match a payment you received", kind: "bool", section: "income", hint: "Off: every suggested event waits in Inbox." },
  { group: "dividends", key: "auto_confirm_tolerance", label: "Amount difference allowed for auto-confirm", kind: "percent", section: "income", hint: "Covers withholding tax and FX; 35 % by default.", min: 0, max: 1 },
  { group: "dividends", key: "match_window_days", label: "Days between pay date and receipt for a match", kind: "int", section: "income", hint: "1–90.", min: 1, max: 90 },
  { group: "dividends", key: "min_payments_for_estimate", label: "Payments needed before estimating", kind: "int", section: "income", hint: "2–12. Fewer and the holding has no schedule.", min: 2, max: 12 },
  { group: "dividends", key: "assume_growth", label: "Grow estimates by each holding's five-year dividend growth", kind: "bool", section: "income", hint: "Off: estimates repeat the last amount." },
  { group: "dividends", key: "single_source_history", label: "Count dividends only one source reports as history", kind: "bool", section: "income", hint: "Each is labelled unconfirmed and never counts as declared. Off: history needs a confirmed event, a receipt or two sources agreeing." },
  { group: "fair_value", key: "stale_after_days", label: "Fair value stale after (days)", kind: "int", section: "fair_value", hint: "7–730.", min: 7, max: 730 },
  { group: "fair_value", key: "move_alert", label: "Alert when a fair value moves by", kind: "percent", section: "fair_value", hint: "0–100 %.", min: 0, max: 1 },
  { group: "fair_value", key: "drift_band", label: "Drift band for a scenario's growth and margin", kind: "percent", section: "fair_value", hint: "Reported figures outside the band mark the scenario as drifting.", min: 0, max: 1 },
  { group: "fair_value", key: "fair_multiple_min_r2", label: "Fit needed to show a fair multiple (R²)", kind: "number", section: "fair_value", hint: "0–1. Below it the peer regression is not shown.", min: 0, max: 1 },
  { group: "fair_value", key: "agreement_close", label: "Analysts agree closely below a target spread of", kind: "percent", section: "fair_value", hint: "(High target − low target) / mean target.", min: 0, max: 5 },
  { group: "fair_value", key: "agreement_wide", label: "Analysts disagree widely from a target spread of", kind: "percent", section: "fair_value", hint: "Must be above the close-agreement cut.", min: 0, max: 5 },
  { group: "fair_value", key: "consensus_chart_min_snapshots", label: "Snapshots before the target chart is drawn", kind: "int", section: "fair_value", hint: "2–104. Fewer and only the table shows.", min: 2, max: 104 },
];

export type ChecksForm = { values: Record<string, string>; tierLow: string; tierHigh: string };

const id = (f: Pick<FieldSpec, "group" | "key">) => `${f.group}.${f.key}`;

function toText(v: unknown, kind: FieldKind): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v !== "number") return "";
  if (kind === "percent") return String(Number((v * 100).toFixed(6)));
  return String(v);
}

export function checksFromBody(body: ChecksConfig): ChecksForm {
  const values: Record<string, string> = {};
  for (const f of FIELDS) {
    const group = body[f.group] as unknown as Record<string, unknown>;
    values[id(f)] = toText(group?.[f.key], f.kind);
  }
  const cuts = body.dividends?.tier_cuts ?? [1 / 3, 2 / 3];
  return { values, tierLow: toText(cuts[0], "percent"), tierHigh: toText(cuts[1], "percent") };
}

function parse(text: string, kind: FieldKind): number | boolean | null {
  if (kind === "bool") return text === "true";
  const t = text.trim().replaceAll(",", "");
  if (t === "") return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  return kind === "percent" ? n / 100 : n;
}

export function checksToBody(form: ChecksForm, base: ChecksConfig): ChecksConfig {
  const out = { schema_version: 1, limits: { ...base.limits }, dividends: { ...base.dividends }, fair_value: { ...base.fair_value } } as unknown as Record<Group, Record<string, unknown>> & { schema_version: 1 };
  for (const f of FIELDS) {
    const v = parse(form.values[id(f)] ?? "", f.kind);
    if (v !== null) out[f.group][f.key] = f.kind === "int" && typeof v === "number" ? Math.round(v) : v;
  }
  const lo = parse(form.tierLow, "percent");
  const hi = parse(form.tierHigh, "percent");
  if (typeof lo === "number" && typeof hi === "number") out.dividends.tier_cuts = [lo, hi];
  return out as unknown as ChecksConfig;
}

/** Client-side bounds, keyed by the backend's error path ("limits.roe_floor"). */
export function validateChecksForm(form: ChecksForm): Record<string, string[]> {
  const errors: Record<string, string[]> = {};
  for (const f of FIELDS) {
    if (f.kind === "bool") continue;
    const v = parse(form.values[id(f)] ?? "", f.kind);
    const k = id(f);
    if (v === null || typeof v !== "number") {
      errors[k] = ["Enter a number."];
      continue;
    }
    const shown = (x: number) => (f.kind === "percent" ? `${Number((x * 100).toFixed(4))} %` : String(x));
    if (f.kind === "int" && !Number.isInteger(v)) errors[k] = ["Enter a whole number."];
    else if (f.min !== undefined && v < f.min) errors[k] = [`At least ${shown(f.min)}.`];
    else if (f.max !== undefined && v > f.max) errors[k] = [`At most ${shown(f.max)}.`];
    else if (f.above !== undefined && v <= f.above) errors[k] = [`Above ${shown(f.above)}.`];
  }
  const lo = parse(form.tierLow, "percent");
  const hi = parse(form.tierHigh, "percent");
  if (typeof lo !== "number" || typeof hi !== "number" || !(lo > 0 && lo < hi && hi < 1)) {
    errors["dividends.tier_cuts"] = ["The low cut must be above 0 % and below the high cut, which must be below 100 %."];
  }
  const close = parse(form.values["fair_value.agreement_close"] ?? "", "percent");
  const wide = parse(form.values["fair_value.agreement_wide"] ?? "", "percent");
  if (typeof close === "number" && typeof wide === "number" && close >= wide && !errors["fair_value.agreement_wide"]) {
    errors["fair_value.agreement_wide"] = ["Must be above the close-agreement cut."];
  }
  return errors;
}

export const fieldPath = id;
