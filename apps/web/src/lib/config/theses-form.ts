import type {
  ThesesConfig,
  ThesisConfig,
  ThesisKPI,
  ThesisPriceTarget,
} from "@/lib/api/client";

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

export const UNIT_EXAMPLES = [
  "%",
  "x",
  "count",
  "USD",
  "USD m",
  "USD bn",
  "EUR m",
  "EUR bn",
  "USD per share",
];

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
  target: TargetForm;
  rigor: RigorForm;
};

export type MainReasonValue = NonNullable<ThesisConfig["main_reason"]>;
export const MAIN_REASONS: readonly { value: MainReasonValue; label: string }[] = [
  { value: "quality", label: "Quality" },
  { value: "value", label: "Value" },
  { value: "catalyst", label: "Catalyst" },
  { value: "momentum", label: "Momentum" },
  { value: "other", label: "Other" },
];

/** The strategy's thesis requirements: what the price assumes, how likely, what could go wrong. */
export type RigorForm = {
  implied_growth: string; // percent a year, "" for unset
  base_share: string; // percent of comparable companies that achieved it
  base_comparison: string;
  base_source: string;
  pre_mortem: string; // one reason per line, at most three
  belief: string;
  disproof: string;
  conviction: string; // "1".."5" or ""
  main_reason: string; // a MainReasonValue or ""
};

export const EMPTY_RIGOR: RigorForm = {
  implied_growth: "",
  base_share: "",
  base_comparison: "",
  base_source: "",
  pre_mortem: "",
  belief: "",
  disproof: "",
  conviction: "",
  main_reason: "",
};

/** A percent typed as "8" or "8,5" as a fraction; null when blank or not a number. */
export function percentToFraction(text: string): number | null {
  const value = parseThreshold(text);
  return value === null ? null : value / 100;
}

const percentText = (fraction: number | null | undefined): string =>
  fraction === null || fraction === undefined ? "" : String(+(fraction * 100).toFixed(4));

export function rigorFromBody(thesis: ThesisConfig): RigorForm {
  return {
    implied_growth: percentText(thesis.implied_growth),
    base_share: percentText(thesis.base_rate?.share_achieved),
    base_comparison: thesis.base_rate?.comparison ?? "",
    base_source: thesis.base_rate?.source ?? "",
    pre_mortem: (thesis.pre_mortem ?? []).join("\n"),
    belief: thesis.variant_perception?.belief ?? "",
    disproof: thesis.variant_perception?.disproof ?? "",
    conviction: thesis.conviction == null ? "" : String(thesis.conviction),
    main_reason: thesis.main_reason ?? "",
  };
}

/** The new thesis fields for the API; a group left blank is sent as null, a half-filled one as typed. */
export function rigorToBody(form: RigorForm): Pick<
  ThesisConfig,
  "implied_growth" | "base_rate" | "pre_mortem" | "variant_perception" | "conviction" | "main_reason"
> {
  const baseBlank = [form.base_share, form.base_comparison, form.base_source].every((v) => !v.trim());
  const variantBlank = [form.belief, form.disproof].every((v) => !v.trim());
  return {
    implied_growth: percentToFraction(form.implied_growth),
    base_rate: baseBlank
      ? null
      : {
          share_achieved: percentToFraction(form.base_share) as number,
          comparison: form.base_comparison.trim(),
          source: form.base_source.trim(),
        },
    pre_mortem: lines(form.pre_mortem),
    variant_perception: variantBlank ? null : { belief: form.belief.trim(), disproof: form.disproof.trim() },
    conviction: form.conviction === "" ? null : Number(form.conviction),
    main_reason: (form.main_reason || null) as MainReasonValue | null,
  };
}

/** Add each pre-mortem reason to the exit conditions, once, keeping what is already there. */
export function preMortemAsExits(exitConditions: string, preMortem: string): string {
  const existing = lines(exitConditions);
  const known = new Set(existing.map((l) => l.toLowerCase()));
  const added = lines(preMortem).filter((l) => !known.has(l.toLowerCase()));
  return [...existing, ...added].join("\n");
}

/** What the strategy asks of a thesis and this one lacks, in plain words. */
export function rigorGaps(form: RigorForm): string[] {
  const gaps: string[] = [];
  if (!form.implied_growth.trim()) gaps.push("the growth the price implies");
  if (!form.base_share.trim() || !form.base_comparison.trim() || !form.base_source.trim()) gaps.push("a base-rate check with its source");
  if (lines(form.pre_mortem).length < 3) gaps.push("three pre-mortem reasons");
  if (!form.belief.trim() || !form.disproof.trim()) gaps.push("a variant perception and what would disprove it");
  if (!form.conviction) gaps.push("a conviction level");
  if (!form.main_reason) gaps.push("the main reason for owning it");
  return gaps;
}

/** The thesis price target; all fields blank means no target. */
export type TargetForm = {
  sell_target: string;
  buy_target: string;
  bear_case: string;
  currency: string;
  method: string;
  set_on: string; // yyyy-mm-dd or ""
};

export const EMPTY_TARGET: TargetForm = {
  sell_target: "",
  buy_target: "",
  bear_case: "",
  currency: "",
  method: "",
  set_on: "",
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
  return {
    key: nextKey(),
    ticker,
    why_owned: "",
    exit_conditions: "",
    review_date: "",
    kpis: [],
    target: EMPTY_TARGET,
    rigor: EMPTY_RIGOR,
  };
}

export function targetFromBody(
  target: ThesisPriceTarget | null | undefined,
): TargetForm {
  if (!target) return EMPTY_TARGET;
  return {
    sell_target: target.sell_target == null ? "" : String(target.sell_target),
    buy_target: target.buy_target == null ? "" : String(target.buy_target),
    bear_case: target.bear_case == null ? "" : String(target.bear_case),
    currency: target.currency,
    method: target.method,
    set_on: target.set_on,
  };
}

/** Null when every field is blank; otherwise sent as typed, so the API's 422 reports gaps. */
export function targetToBody(form: TargetForm): ThesisPriceTarget | null {
  if (Object.values(form).every((v) => !v.trim())) return null;
  return {
    sell_target: parseThreshold(form.sell_target),
    buy_target: parseThreshold(form.buy_target),
    bear_case: parseThreshold(form.bear_case),
    currency: form.currency.trim().toUpperCase(),
    method: form.method.trim(),
    set_on: form.set_on,
  };
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
    target: targetFromBody(thesis.price_target),
    rigor: rigorFromBody(thesis),
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
    theses: forms.map((form): ThesisConfig => ({
      ticker: form.ticker.trim(),
      why_owned: form.why_owned.trim(),
      exit_conditions: lines(form.exit_conditions),
      review_date: form.review_date || null,
      kpis: form.kpis.map((kpi): ThesisKPI => ({
        id: kpi.id.trim(),
        metric: kpi.metric.trim(),
        operator: kpi.operator,
        threshold: parseThreshold(kpi.threshold) as number,
        unit: kpi.unit.trim(),
        basis: kpi.basis,
        period: kpi.period,
        description: kpi.description.trim(),
      })),
      price_target: targetToBody(form.target),
      ...rigorToBody(form.rigor),
    })),
  };
}
