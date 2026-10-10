import type { StrategyConfig, StrategyRule } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";

/*
 * Form state for the strategy config and the conversion to and from `StrategyConfig`
 * (generated from the backend's Pydantic model).
 *
 * Weights are stored as fractions (0.10) but typed as percentages (10). The conversion
 * shifts the decimal point in the number's shortest decimal text instead of multiplying,
 * so 0.07 shows as "7" (not 7.000000000000001) and "7" saves as exactly 0.07: every
 * stored weight round-trips without loss. Text that is not a number is sent as typed, so
 * the backend's 422 answer reports it next to the field.
 */

export type RuleType = StrategyRule["type"];
export type Operator = StrategyRule["operator"];
export type OnBreach = NonNullable<StrategyRule["on_breach"]>;
export type FxChoice = "default" | "cnb" | "ecb";
export type WeightBasis = StrategyConfig["weight_basis"];

export const RULE_TYPES: readonly { value: RuleType; label: string; hint: string }[] = [
  { value: "position_weight", label: "Position weight", hint: "One position's weight; leave the ticker empty to check every position." },
  { value: "category_weight", label: "Category weight", hint: "A category's weight; leave the bucket empty to check every bucket of the dimension." },
  { value: "cash_weight", label: "Cash weight", hint: "Cash as a share of the portfolio." },
  { value: "position_count", label: "Position count", hint: "How many positions are open." },
];
export const RULE_OPERATORS: readonly Operator[] = ["<", "<=", ">", ">=", "=="];
export const ON_BREACH: readonly { value: OnBreach; label: string }[] = [
  { value: "severity_1", label: "Severity 1 · digest" },
  { value: "severity_2", label: "Severity 2 · digest highlight" },
  { value: "severity_3", label: "Severity 3 · instant alert" },
];
export const FX_CHOICES: readonly { value: FxChoice; label: string }[] = [
  { value: "default", label: "Default (ČNB for CZK, ECB otherwise)" },
  { value: "cnb", label: "ČNB (Czech National Bank)" },
  { value: "ecb", label: "ECB (European Central Bank)" },
];
export const WEIGHT_BASES: readonly { value: WeightBasis; label: string }[] = [
  { value: "total", label: "Total: positions plus cash" },
  { value: "invested", label: "Invested: positions only" },
];

/** The backend's defaults (StrategyConfig with a CZK base), used when nothing is saved yet. */
export const DEFAULT_STRATEGY: StrategyConfig = {
  schema_version: 1,
  base_currency: "CZK",
  fx_source: null,
  weight_basis: "total",
  goals: "",
  horizon_years: null,
  notes: "",
  categories: ["sector", "geography", "currency"],
  classifications: {},
  cash_target: null,
  target_positions: [],
  target_categories: [],
  rules: [],
  risk: {
    max_volatility: null,
    max_beta: null,
    position_loss_review: null,
    max_look_through_weight: null,
    max_sector_weight: null,
    max_country_weight: null,
    max_drawdown: null,
    max_pair_correlation: null,
    correlation_min_combined_weight: 0.1,
    max_var_95: null,
    max_tracking_error: null,
    max_stress_loss: null,
    stress_equity_fall: 0.2,
    credit_spread_alert_bp: null,
    max_valuation_terminal_growth: null,
    max_valuation_growth: null,
    on_breach: "severity_2",
    auto_tiers: false,
    tier_speculative_market_cap: 2e9,
    tier_core_min_market_cap: 1e10,
  },
  hard_limit_severity: "severity_3",
  band_breach_severity: "severity_1",
};

let counter = 0;
const nextKey = () => `s${++counter}`;

// ---------------------------------------------------------------------------
// Percent <-> fraction, exact
// ---------------------------------------------------------------------------

/** Moves the decimal point of a decimal numeral `places` to the right (negative: left). */
export function shiftDecimal(numeral: string, places: number): string | null {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(numeral.trim());
  if (!match || (match[2] === "" && (match[3] ?? "") === "")) return null;
  const [, sign, whole, fraction = "", exponent = "0"] = match;
  const digits = whole + fraction;
  const point = whole.length + Number(exponent) + places;
  let text: string;
  if (point <= 0) text = `0.${"0".repeat(-point)}${digits}`;
  else if (point >= digits.length) text = digits + "0".repeat(point - digits.length);
  else text = `${digits.slice(0, point)}.${digits.slice(point)}`;
  // Tidy: no leading zeros before the point, no trailing zeros after it.
  let [intPart, fracPart = ""] = text.split(".");
  intPart = intPart.replace(/^0+(?=\d)/, "") || "0";
  fracPart = fracPart.replace(/0+$/, "");
  const result = fracPart ? `${intPart}.${fracPart}` : intPart;
  return sign === "-" && /[1-9]/.test(result) ? `-${result}` : result;
}

/** 0.07 -> "7", 0.035 -> "3.5". */
export function fractionToPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return shiftDecimal(String(value), 2) ?? String(value);
}

/** "7" -> 0.07, "3,5 %" -> 0.035. Anything else is returned as typed, for the backend to reject. */
export function percentToFraction(text: string): number | string {
  const cleaned = text.trim().replace(/\s*%$/, "").replace(",", ".");
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return text.trim();
  const shifted = shiftDecimal(cleaned, -2);
  return shifted === null ? text.trim() : Number(shifted);
}

/** Blank -> null (limit off); otherwise a percentage as a fraction. */
function optionalPercent(text: string): number | string | null {
  return text.trim() === "" ? null : percentToFraction(text);
}

/** Blank -> null (limit off); otherwise a plain number, or the text as typed for the backend to reject. */
function optionalNumber(text: string): number | string | null {
  const trimmed = text.trim().replace(",", ".");
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : trimmed;
}

function integerOrText(text: string): number | string {
  const trimmed = text.trim();
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : trimmed;
}

// ---------------------------------------------------------------------------
// Form state
// ---------------------------------------------------------------------------

export type CategoryForm = { key: string; name: string };
/** One holding's buckets, keyed by the category's form key (so renaming a dimension keeps them). */
export type ClassificationForm = { key: string; ticker: string; buckets: Record<string, string> };
export type PositionTargetForm = { key: string; ticker: string; target_weight: string; band: string; hard_limit: string };
export type CategoryTargetForm = { key: string; dimension: string; bucket: string; target_weight: string; band: string };
export type RuleForm = {
  key: string;
  type: RuleType;
  id: string;
  operator: Operator;
  on_breach: OnBreach;
  description: string;
  /** Disabled rules are kept in the config but skipped by breach evaluation. */
  enabled: boolean;
  /** A percentage for weight rules, a whole number for position_count. */
  value: string;
  ticker: string;
  dimension: string;
  bucket: string;
};

export type StrategyForm = {
  base_currency: string;
  fx_source: FxChoice;
  weight_basis: WeightBasis;
  goals: string;
  horizon_years: string;
  notes: string;
  categories: CategoryForm[];
  classifications: ClassificationForm[];
  cash_enabled: boolean;
  cash_target_weight: string;
  cash_band: string;
  target_positions: PositionTargetForm[];
  target_categories: CategoryTargetForm[];
  rules: RuleForm[];
  /** Risk limits; blank means the limit is off. Percentages except the beta ceiling. */
  risk_max_volatility: string;
  risk_max_beta: string;
  risk_position_loss_review: string;
  risk_max_look_through_weight: string;
  risk_max_sector_weight: string;
  risk_max_country_weight: string;
  risk_max_drawdown: string;
  risk_max_pair_correlation: string;
  risk_correlation_min_combined_weight: string;
  risk_max_var_95: string;
  risk_max_tracking_error: string;
  risk_max_stress_loss: string;
  risk_stress_equity_fall: string;
  risk_credit_spread_alert_bp: string;
  risk_max_valuation_terminal_growth: string;
  risk_max_valuation_growth: string;
  risk_on_breach: OnBreach;
  /** Severity of a position above its hard limit. */
  hard_limit_severity: OnBreach;
  /** Severity of a weight outside its band (1: digest only; 2 or 3 also alert). */
  band_breach_severity: OnBreach;
  /** Pre-trade thresholds. The editor has no fields for them; they are kept as loaded so a
   *  save never resets them to the defaults. */
  pretrade?: StrategyConfig["pretrade"];
  /** Risk settings the editor has no fields for (tier budgets and thresholds, derived tiers,
   *  extra stress episodes, the effective-bets floor); kept as loaded so a save never drops them. */
  risk_extra: RiskExtra;
};

const RISK_EXTRA_KEYS = [
  "tier_risk_budgets",
  "min_effective_bets",
  "tier_speculative_volatility",
  "tier_core_max_volatility",
  "auto_tiers",
  "tier_speculative_market_cap",
  "tier_core_min_market_cap",
  "stress_episodes",
  "drawdown_warnings",
] as const;

type RiskBody = NonNullable<StrategyConfig["risk"]>;
type RiskExtra = Partial<Pick<RiskBody, (typeof RISK_EXTRA_KEYS)[number]>>;

function pickRiskExtra(risk: Partial<RiskBody> | undefined): RiskExtra {
  const out: Record<string, unknown> = {};
  for (const key of RISK_EXTRA_KEYS) if (risk?.[key] !== undefined) out[key] = risk[key];
  return out as RiskExtra;
}

export const newCategory = (name = ""): CategoryForm => ({ key: nextKey(), name });
export const newClassification = (ticker = ""): ClassificationForm => ({ key: nextKey(), ticker, buckets: {} });
export const newPositionTarget = (ticker = ""): PositionTargetForm => ({
  key: nextKey(),
  ticker,
  target_weight: "",
  band: "2",
  hard_limit: "",
});
export const newCategoryTarget = (dimension = ""): CategoryTargetForm => ({
  key: nextKey(),
  dimension,
  bucket: "",
  target_weight: "",
  band: "2",
});
export function newRule(type: RuleType = "position_weight"): RuleForm {
  return {
    key: nextKey(),
    type,
    id: "",
    operator: "<=",
    on_breach: "severity_2",
    description: "",
    enabled: true,
    value: "",
    ticker: "",
    dimension: "",
    bucket: "",
  };
}

export function strategyFromBody(body: StrategyConfig | null): StrategyForm {
  const b = body ?? DEFAULT_STRATEGY;
  const categories = (b.categories ?? []).map((name) => newCategory(name));
  const keyOf = new Map(categories.map((c) => [c.name, c.key]));
  const classifications = Object.entries(b.classifications ?? {}).map(([ticker, buckets]) => {
    const row = newClassification(ticker);
    for (const [dimension, bucket] of Object.entries(buckets)) {
      let key = keyOf.get(dimension);
      if (!key) {
        // A dimension the categories do not declare (the backend rejects it): keep it
        // visible as a category so nothing is dropped silently.
        const added = newCategory(dimension);
        categories.push(added);
        keyOf.set(dimension, added.key);
        key = added.key;
      }
      row.buckets[key] = bucket;
    }
    return row;
  });
  return {
    base_currency: b.base_currency,
    fx_source: b.fx_source ?? "default",
    weight_basis: b.weight_basis ?? "total",
    goals: b.goals ?? "",
    horizon_years: b.horizon_years == null ? "" : String(b.horizon_years),
    notes: b.notes ?? "",
    hard_limit_severity: b.hard_limit_severity ?? "severity_3",
    band_breach_severity: b.band_breach_severity ?? "severity_1",
    pretrade: b.pretrade,
    risk_extra: pickRiskExtra(b.risk),
    risk_max_volatility: fractionToPercent(b.risk?.max_volatility),
    risk_max_beta: b.risk?.max_beta == null ? "" : String(b.risk.max_beta),
    risk_position_loss_review: fractionToPercent(b.risk?.position_loss_review),
    risk_max_look_through_weight: fractionToPercent(b.risk?.max_look_through_weight),
    risk_max_sector_weight: fractionToPercent(b.risk?.max_sector_weight),
    risk_max_country_weight: fractionToPercent(b.risk?.max_country_weight),
    risk_max_drawdown: fractionToPercent(b.risk?.max_drawdown),
    risk_max_pair_correlation: b.risk?.max_pair_correlation == null ? "" : String(b.risk.max_pair_correlation),
    risk_correlation_min_combined_weight: fractionToPercent(b.risk?.correlation_min_combined_weight ?? 0.1),
    risk_max_var_95: fractionToPercent(b.risk?.max_var_95),
    risk_max_tracking_error: fractionToPercent(b.risk?.max_tracking_error),
    risk_max_stress_loss: fractionToPercent(b.risk?.max_stress_loss),
    risk_stress_equity_fall: fractionToPercent(b.risk?.stress_equity_fall ?? 0.2),
    risk_credit_spread_alert_bp: b.risk?.credit_spread_alert_bp == null ? "" : String(b.risk.credit_spread_alert_bp),
    risk_max_valuation_terminal_growth: fractionToPercent(b.risk?.max_valuation_terminal_growth),
    risk_max_valuation_growth: fractionToPercent(b.risk?.max_valuation_growth),
    risk_on_breach: b.risk?.on_breach ?? "severity_2",
    categories,
    classifications,
    cash_enabled: b.cash_target != null,
    cash_target_weight: fractionToPercent(b.cash_target?.target_weight),
    cash_band: fractionToPercent(b.cash_target?.band ?? 0.02),
    target_positions: (b.target_positions ?? []).map((p) => ({
      key: nextKey(),
      ticker: p.ticker,
      target_weight: fractionToPercent(p.target_weight),
      band: fractionToPercent(p.band ?? 0.02),
      hard_limit: fractionToPercent(p.hard_limit),
    })),
    target_categories: (b.target_categories ?? []).map((c) => ({
      key: nextKey(),
      dimension: c.dimension,
      bucket: c.bucket,
      target_weight: fractionToPercent(c.target_weight),
      band: fractionToPercent(c.band ?? 0.02),
    })),
    rules: (b.rules ?? []).map((r) => ({
      ...newRule(r.type),
      id: r.id,
      operator: r.operator,
      on_breach: r.on_breach ?? "severity_2",
      description: r.description ?? "",
      enabled: r.enabled ?? true,
      value: r.type === "position_count" ? String(r.value) : fractionToPercent(r.value),
      ticker: r.type === "position_weight" ? (r.ticker ?? "") : "",
      dimension: r.type === "category_weight" ? r.dimension : "",
      bucket: r.type === "category_weight" ? (r.bucket ?? "") : "",
    })),
  };
}

function ruleToBody(r: RuleForm): StrategyRule {
  const common = {
    id: r.id.trim(),
    operator: r.operator,
    on_breach: r.on_breach,
    description: r.description,
    enabled: r.enabled,
  };
  switch (r.type) {
    case "position_weight":
      return { ...common, type: r.type, value: percentToFraction(r.value) as number, ticker: r.ticker.trim() || null };
    case "category_weight":
      return {
        ...common,
        type: r.type,
        value: percentToFraction(r.value) as number,
        dimension: r.dimension.trim(),
        bucket: r.bucket.trim() || null,
      };
    case "cash_weight":
      return { ...common, type: r.type, value: percentToFraction(r.value) as number };
    case "position_count":
      return { ...common, type: r.type, value: integerOrText(r.value) as number };
  }
}

export function strategyToBody(form: StrategyForm): StrategyConfig {
  const nameOf = new Map(form.categories.map((c) => [c.key, c.name.trim()]));
  return {
    schema_version: 1,
    base_currency: form.base_currency.trim().toUpperCase(),
    fx_source: form.fx_source === "default" ? null : form.fx_source,
    weight_basis: form.weight_basis,
    goals: form.goals,
    horizon_years: form.horizon_years.trim() === "" ? null : (integerOrText(form.horizon_years) as number),
    notes: form.notes,
    categories: form.categories.map((c) => c.name.trim()),
    classifications: Object.fromEntries(
      form.classifications.map((row) => [
        row.ticker.trim(),
        Object.fromEntries(
          form.categories
            .map((c) => [nameOf.get(c.key) ?? "", (row.buckets[c.key] ?? "").trim()] as const)
            .filter(([name, bucket]) => name !== "" && bucket !== ""),
        ),
      ]),
    ),
    cash_target: form.cash_enabled
      ? {
          target_weight: percentToFraction(form.cash_target_weight) as number,
          band: percentToFraction(form.cash_band) as number,
        }
      : null,
    target_positions: form.target_positions.map((p) => ({
      ticker: p.ticker.trim(),
      target_weight: percentToFraction(p.target_weight) as number,
      band: percentToFraction(p.band) as number,
      hard_limit: p.hard_limit.trim() === "" ? null : (percentToFraction(p.hard_limit) as number),
    })),
    target_categories: form.target_categories.map((c) => ({
      dimension: c.dimension.trim(),
      bucket: c.bucket.trim(),
      target_weight: percentToFraction(c.target_weight) as number,
      band: percentToFraction(c.band) as number,
    })),
    rules: form.rules.map(ruleToBody),
    risk: {
      max_volatility: optionalPercent(form.risk_max_volatility) as number | null,
      max_beta: optionalNumber(form.risk_max_beta) as number | null,
      position_loss_review: optionalPercent(form.risk_position_loss_review) as number | null,
      max_look_through_weight: optionalPercent(form.risk_max_look_through_weight) as number | null,
      max_sector_weight: optionalPercent(form.risk_max_sector_weight) as number | null,
      max_country_weight: optionalPercent(form.risk_max_country_weight) as number | null,
      max_drawdown: optionalPercent(form.risk_max_drawdown) as number | null,
      max_pair_correlation: optionalNumber(form.risk_max_pair_correlation) as number | null,
      correlation_min_combined_weight: percentToFraction(form.risk_correlation_min_combined_weight) as number,
      max_var_95: optionalPercent(form.risk_max_var_95) as number | null,
      max_tracking_error: optionalPercent(form.risk_max_tracking_error) as number | null,
      max_stress_loss: optionalPercent(form.risk_max_stress_loss) as number | null,
      stress_equity_fall: percentToFraction(form.risk_stress_equity_fall) as number,
      credit_spread_alert_bp: optionalNumber(form.risk_credit_spread_alert_bp) as number | null,
      max_valuation_terminal_growth: optionalPercent(form.risk_max_valuation_terminal_growth) as number | null,
      max_valuation_growth: optionalPercent(form.risk_max_valuation_growth) as number | null,
      on_breach: form.risk_on_breach,
      auto_tiers: false,
      tier_speculative_market_cap: 2e9,
      tier_core_min_market_cap: 1e10,
      ...form.risk_extra,
    },
    hard_limit_severity: form.hard_limit_severity,
    band_breach_severity: form.band_breach_severity,
    ...(form.pretrade ? { pretrade: form.pretrade } : {}),
  };
}

// ---------------------------------------------------------------------------
// Checks before saving
// ---------------------------------------------------------------------------

const TOLERANCE = 1e-9;

function percentLabel(fraction: number): string {
  const rounded = Math.round(fraction * 10000) / 100;
  return `${rounded} %`;
}

/** Sum of the numeric weights in a list (text that is not a number counts as 0). */
function sumWeights(values: (number | string)[]): number {
  return values.reduce<number>((total, v) => total + (typeof v === "number" && Number.isFinite(v) ? v : 0), 0);
}

export type TargetSums = {
  positionsAndCash: number;
  byDimension: Record<string, number>;
};

export function targetSums(body: StrategyConfig): TargetSums {
  const positions = sumWeights((body.target_positions ?? []).map((p) => p.target_weight));
  const cash = body.cash_target ? sumWeights([body.cash_target.target_weight]) : 0;
  const byDimension: Record<string, number> = {};
  for (const c of body.target_categories ?? []) {
    byDimension[c.dimension] = (byDimension[c.dimension] ?? 0) + sumWeights([c.target_weight]);
  }
  return { positionsAndCash: positions + cash, byDimension };
}

/**
 * Targets summing above 100 %, as field errors on each target weight involved (the same
 * rule the backend's model validator applies, so Save is blocked before a round trip).
 */
export function targetSumErrors(body: StrategyConfig): FieldErrors {
  const errors: FieldErrors = {};
  const sums = targetSums(body);
  if (sums.positionsAndCash > 1 + TOLERANCE) {
    const message = `Position and cash targets sum to ${percentLabel(sums.positionsAndCash)}, above 100 %.`;
    (body.target_positions ?? []).forEach((_, i) => {
      errors[`target_positions.${i}.target_weight`] = [message];
    });
    if (body.cash_target) errors["cash_target.target_weight"] = [message];
  }
  (body.target_categories ?? []).forEach((c, i) => {
    const total = sums.byDimension[c.dimension] ?? 0;
    if (total > 1 + TOLERANCE) {
      errors[`target_categories.${i}.target_weight`] = [
        `Targets for “${c.dimension}” sum to ${percentLabel(total)}, above 100 %.`,
      ];
    }
  });
  return errors;
}

const RULE_TAGS = /^(rules\.\d+)\.(?:position_weight|category_weight|cash_weight|position_count)(?=\.|$)/;

/**
 * Pydantic reports errors inside a discriminated union with the tag in the path
 * ("rules.0.position_weight.value"); drop it so the error lands on the rule's field.
 */
export function normalizeStrategyErrors(errors: FieldErrors): FieldErrors {
  const out: FieldErrors = {};
  for (const [path, messages] of Object.entries(errors)) {
    const normalized = path.replace(RULE_TAGS, "$1");
    (out[normalized] ??= []).push(...messages);
  }
  return out;
}

/** Field errors from both sources, client-side first. */
export function mergeErrors(...sources: FieldErrors[]): FieldErrors {
  const out: FieldErrors = {};
  for (const source of sources) {
    for (const [path, messages] of Object.entries(source)) {
      for (const m of messages) if (!(out[path] ??= []).includes(m)) out[path].push(m);
    }
  }
  return out;
}
