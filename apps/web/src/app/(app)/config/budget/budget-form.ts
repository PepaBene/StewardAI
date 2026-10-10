import { normaliseSeparators } from "@/lib/number-input";
import type { FieldErrors } from "@/lib/api/validation-errors";
import { fractionToPercent, percentToFraction } from "@/lib/config/strategy-form";

import { JOB_TYPES, type AtCap, type BudgetConfig, type JobType } from "./budget-api";

/*
 * Form state for the Budget config (apps/backend/src/steward/config/budget.py): the price
 * table every AI step is costed with, and the spend limits the budget guard enforces before
 * each AI request of a job. The warning threshold is typed as a percentage; money in USD.
 * Fields this form does not know are kept, so a field the backend adds later survives a save.
 * `validateBudgetForm` mirrors the backend's bounds, so a mistake shows next to its field
 * before anything is sent.
 */

export const DEFAULT_CRITICAL: JobType[] = ["earnings_check", "event_escalation", "chat"];

export const AT_CAP_OPTIONS: readonly { value: AtCap; label: string }[] = [
  {
    value: "pause_noncritical",
    label: "Pause non-critical jobs (they fail before their next AI request)",
  },
  {
    value: "downgrade_noncritical",
    label: "Downgrade non-critical jobs to a cheaper model",
  },
  { value: "alert_only", label: "Only alert; every job keeps running" },
];

export const JOB_TYPE_LABELS: Record<JobType, string> = Object.fromEntries(
  JOB_TYPES.map((t) => [
    t,
    t
      .replaceAll("_", " ")
      .replace(/^./, (c) => c.toUpperCase())
      .replace("13f", "13F"),
  ]),
) as Record<JobType, string>;

/** The backend's default prices, for when nothing is saved yet (a 404). */
const DEFAULT_PRICES: [string, number, number][] = [
  ["claude-fable-5-1", 10, 50],
  ["claude-opus-4-8", 5, 25],
  ["claude-haiku-4-5", 1, 5],
  ["claude-sonnet-5", 2, 10],
  ["claude-sonnet-4-6", 3, 15],
  ["claude-opus-5", 5, 25],
  ["claude-opus-5-5", 4, 20],
];

export const DEFAULT_BUDGET: BudgetConfig = {
  schema_version: 1,
  prices: DEFAULT_PRICES.map(([model, input_per_mtok, output_per_mtok]) => ({
    model,
    input_per_mtok,
    output_per_mtok,
  })),
  web_search_per_1000: 10,
  cache_write_multiplier: 1.25,
  cache_read_multiplier: 0.1,
  batch_multiplier: 0.5,
  monthly_cap_usd: null,
  warn_at_fraction: 0.8,
  at_cap: "pause_noncritical",
  downgrade_model: "claude-haiku-4-5",
  critical_job_types: DEFAULT_CRITICAL,
  per_job_cap_usd: {},
  default_job_cap_usd: null,
};

export type PriceRow = {
  key: string;
  model: string;
  input: string;
  output: string;
};
export type JobCapRow = { key: string; jobType: JobType | ""; cap: string };

export type BudgetForm = {
  prices: PriceRow[];
  webSearch: string;
  cacheWrite: string;
  cacheRead: string;
  batch: string;
  monthlyCap: string;
  /** Percent: "80" = alert at 80 % of the cap. */
  warnPercent: string;
  atCap: AtCap;
  downgradeModel: string;
  critical: JobType[];
  jobCaps: JobCapRow[];
  /** Empty: no default cap per run. */
  defaultJobCap: string;
  rest: Record<string, unknown>;
};

let counter = 0;
const nextKey = () => `r${++counter}`;
export const newPriceRow = (): PriceRow => ({
  key: nextKey(),
  model: "",
  input: "",
  output: "",
});
export const newJobCapRow = (): JobCapRow => ({
  key: nextKey(),
  jobType: "",
  cap: "",
});

const text = (v: number | null | undefined) => (v === null || v === undefined ? "" : String(v));

const KNOWN = new Set(Object.keys(DEFAULT_BUDGET));

export function budgetFromBody(body: BudgetConfig | null): BudgetForm {
  const b = { ...DEFAULT_BUDGET, ...(body ?? {}) };
  return {
    prices: (b.prices ?? []).map((p) => ({
      key: nextKey(),
      model: p.model,
      input: String(p.input_per_mtok),
      output: String(p.output_per_mtok),
    })),
    webSearch: text(b.web_search_per_1000),
    cacheWrite: text(b.cache_write_multiplier),
    cacheRead: text(b.cache_read_multiplier),
    batch: text(b.batch_multiplier),
    monthlyCap: text(b.monthly_cap_usd),
    warnPercent: fractionToPercent(b.warn_at_fraction ?? 0.8),
    atCap: b.at_cap ?? "pause_noncritical",
    downgradeModel: b.downgrade_model ?? "claude-haiku-4-5",
    critical: [...(b.critical_job_types ?? DEFAULT_CRITICAL)],
    jobCaps: Object.entries(b.per_job_cap_usd ?? {}).map(([jobType, cap]) => ({
      key: nextKey(),
      jobType: jobType as JobType,
      cap: text(cap),
    })),
    defaultJobCap: text(b.default_job_cap_usd),
    rest: Object.fromEntries(Object.entries(body ?? {}).filter(([k]) => !KNOWN.has(k))),
  };
}

/** A typed number, or the text as typed for the backend to reject. */
function num(value: string): number {
  const cleaned = normaliseSeparators(value);
  const n = Number(cleaned);
  return (cleaned !== "" && Number.isFinite(n) ? n : value.trim()) as number;
}

const optionalNum = (value: string): number | null => (value.trim() === "" ? null : num(value));

export function budgetToBody(form: BudgetForm): BudgetConfig {
  return {
    ...form.rest,
    schema_version: 1,
    prices: form.prices.map((p) => ({
      model: p.model.trim(),
      input_per_mtok: num(p.input),
      output_per_mtok: num(p.output),
    })),
    web_search_per_1000: num(form.webSearch),
    cache_write_multiplier: num(form.cacheWrite),
    cache_read_multiplier: num(form.cacheRead),
    batch_multiplier: num(form.batch),
    monthly_cap_usd: optionalNum(form.monthlyCap),
    warn_at_fraction: percentToFraction(form.warnPercent) as number,
    at_cap: form.atCap,
    downgrade_model: form.downgradeModel.trim(),
    critical_job_types: JOB_TYPES.filter((t) => form.critical.includes(t)),
    per_job_cap_usd: Object.fromEntries(form.jobCaps.map((r) => [r.jobType, num(r.cap)])),
    default_job_cap_usd: optionalNum(form.defaultJobCap),
  };
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function validateBudgetForm(form: BudgetForm): FieldErrors {
  const body = budgetToBody(form);
  const errors: FieldErrors = {};
  const add = (path: string, message: string) => (errors[path] = [...(errors[path] ?? []), message]);
  const seen = new Set<string>();
  (body.prices ?? []).forEach((p, i) => {
    if (!/^claude-[a-z0-9.-]{1,64}$/.test(p.model)) add(`prices.${i}.model`, "A Claude model id, e.g. claude-sonnet-5.");
    else if (seen.has(p.model)) add(`prices.${i}.model`, "This model already has a price.");
    seen.add(p.model);
    if (!isNum(p.input_per_mtok) || p.input_per_mtok < 0) add(`prices.${i}.input_per_mtok`, "A number, 0 or more.");
    if (!isNum(p.output_per_mtok) || p.output_per_mtok < 0) add(`prices.${i}.output_per_mtok`, "A number, 0 or more.");
  });
  for (const key of ["web_search_per_1000", "cache_write_multiplier", "cache_read_multiplier"] as const) {
    const v = body[key];
    if (!isNum(v) || v < 0) add(key, "A number, 0 or more.");
  }
  if (!isNum(body.batch_multiplier) || body.batch_multiplier < 0 || body.batch_multiplier > 1) add("batch_multiplier", "Between 0 and 1.");
  if (body.monthly_cap_usd !== null && (!isNum(body.monthly_cap_usd) || body.monthly_cap_usd <= 0))
    add("monthly_cap_usd", "An amount above 0, or empty for no cap.");
  if (!isNum(body.warn_at_fraction) || body.warn_at_fraction <= 0 || body.warn_at_fraction > 1)
    add("warn_at_fraction", "A percentage above 0 and at most 100.");
  if (!/^claude-[a-z0-9.-]{1,64}$/.test(body.downgrade_model ?? "")) add("downgrade_model", "A Claude model id.");
  else if (body.at_cap === "downgrade_noncritical" && !seen.has(body.downgrade_model ?? ""))
    add("downgrade_model", "Add a price for this model first, so its steps are costed.");
  const jobs = new Set<string>();
  form.jobCaps.forEach((r, i) => {
    if (r.jobType === "") add(`per_job_cap_usd.row${i}`, "Choose a job type.");
    else if (jobs.has(r.jobType)) add(`per_job_cap_usd.row${i}`, "This job type already has a cap.");
    jobs.add(r.jobType);
    const cap = num(r.cap);
    if (!isNum(cap) || cap <= 0) add(`per_job_cap_usd.row${i}`, "An amount above 0.");
  });
  if (body.default_job_cap_usd !== null && (!isNum(body.default_job_cap_usd) || body.default_job_cap_usd <= 0))
    add("default_job_cap_usd", "An amount above 0, or empty for no default cap.");
  return errors;
}

/** The budget month (YYYY-MM) of `now` in Europe/Prague, as the backend counts spend. */
export function currentBudgetMonth(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Prague",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}`;
}
