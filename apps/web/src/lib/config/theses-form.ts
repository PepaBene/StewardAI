import type { ThesesConfig, ThesisConfig, ThesisKPI } from "@/lib/api/client";

/*
 * Editable form state for the theses config, and the conversion to and from the body the
 * API validates (`ThesesConfig`, generated from the backend's Pydantic model). Numbers and
 * lists are kept as the text the owner typed; the API's 422 answer reports anything wrong.
 */

export type Operator = ThesisKPI["operator"];
export type Basis = ThesisKPI["basis"];
export type KpiPeriod = ThesisKPI["period"];

export const OPERATORS: readonly Operator[] = ["<", "<=", ">", ">=", "=="];
export const BASES: readonly Basis[] = ["reported", "adjusted"];
export const KPI_PERIODS: readonly KpiPeriod[] = ["quarter", "annual", "ttm"];

/** Metrics the backend extracts or recomputes; any other snake_case name is extracted as printed. */
export const KNOWN_METRICS = [
  "revenue",
  "revenue_growth_yoy",
  "revenue_growth_qoq",
  "gross_profit",
  "gross_margin",
  "operating_income",
  "operating_margin",
  "net_income",
  "net_margin",
  "eps_diluted",
  "eps_diluted_growth_yoy",
  "free_cash_flow",
  "fcf_margin",
  "operating_cash_flow",
  "ebitda",
  "ebitda_margin",
  "backlog",
  "bookings",
] as const;

export const UNIT_EXAMPLES = ["%", "x", "count", "USD", "USD m", "USD bn", "EUR m", "EUR bn", "USD per share"];

export type KpiForm = {
  key: string; // React key only
  id: string;
  metric: string;
  operator: Operator;
  threshold: string;
  unit: string;
  basis: Basis;
  period: KpiPeriod;
  description: string;
};

export type ThesisForm = {
  key: string;
  ticker: string;
  why_owned: string;
  exit_conditions: string; // one per line
  review_date: string; // yyyy-mm-dd or ""
  kpis: KpiForm[];
};

let counter = 0;
const nextKey = () => `k${++counter}`;

export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** A KPI id from its metric: "revenue_growth_yoy" -> "revenue-growth-yoy". */
export function slugFromMetric(metric: string): string {
  return metric
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

export function newKpi(metric = "revenue_growth_yoy"): KpiForm {
  return {
    key: nextKey(),
    id: slugFromMetric(metric),
    metric,
    operator: ">=",
    threshold: "",
    unit: "%",
    basis: "reported",
    period: "quarter",
    description: "",
  };
}

export function newThesis(ticker = ""): ThesisForm {
  return { key: nextKey(), ticker, why_owned: "", exit_conditions: "", review_date: "", kpis: [] };
}

export function kpiFromBody(kpi: ThesisKPI): KpiForm {
  return {
    key: nextKey(),
    id: kpi.id,
    metric: kpi.metric,
    operator: kpi.operator,
    threshold: String(kpi.threshold),
    unit: kpi.unit,
    basis: kpi.basis,
    period: kpi.period,
    description: kpi.description,
  };
}

export function thesesFromBody(body: ThesesConfig | null): ThesisForm[] {
  return (body?.theses ?? []).map((thesis) => ({
    key: nextKey(),
    ticker: thesis.ticker,
    why_owned: thesis.why_owned,
    exit_conditions: (thesis.exit_conditions ?? []).join("\n"),
    review_date: thesis.review_date ?? "",
    kpis: (thesis.kpis ?? []).map(kpiFromBody),
  }));
}

/** Empty or non-numeric thresholds are sent as null, which the API rejects with a 422. */
export function parseThreshold(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

export function thesesToBody(forms: ThesisForm[]): ThesesConfig {
  return {
    schema_version: 1,
    theses: forms.map(
      (form): ThesisConfig => ({
        ticker: form.ticker.trim(),
        why_owned: form.why_owned.trim(),
        exit_conditions: lines(form.exit_conditions),
        review_date: form.review_date || null,
        kpis: form.kpis.map(
          (kpi): ThesisKPI => ({
            id: kpi.id.trim(),
            metric: kpi.metric.trim(),
            operator: kpi.operator,
            threshold: parseThreshold(kpi.threshold) as number,
            unit: kpi.unit.trim(),
            basis: kpi.basis,
            period: kpi.period,
            description: kpi.description.trim(),
          }),
        ),
      }),
    ),
  };
}
