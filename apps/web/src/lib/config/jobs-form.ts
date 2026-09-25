import type { JobsConfig, JobSchedule } from "@/lib/api/client";

/*
 * Form state for the jobs config (`JobsConfig`, generated from the backend's Pydantic
 * model): one entry per schedule, plus the nested settings of the jobs that call a model.
 *
 * Schedules are found by shape, not by name: every property of the body that looks like a
 * `JobSchedule` (an object with a `cron` string) is listed, so a schedule the backend adds
 * later shows up and round-trips even before this file names it. Settings keep the loaded
 * object and override only the fields the form edits, so a field added later is not lost.
 */

export type ScheduleKey = {
  [K in keyof JobsConfig]-?: NonNullable<JobsConfig[K]> extends JobSchedule ? K : never;
}[keyof JobsConfig];

export type SettingsKey = "earnings_check" | "earnings_calendar_settings" | "news" | "digest";
type Settings<S extends SettingsKey> = NonNullable<JobsConfig[S]>;

/** Labels and the backend's default cron per known schedule (apps/backend/src/steward/config/jobs.py). */
export const SCHEDULES: Record<ScheduleKey, { label: string; description: string; cron: string }> = {
  portfolio_sync: {
    label: "Portfolio sync",
    description: "Trading212 positions and cash, FX rates and drift.",
    cron: "30 22 * * *",
  },
  earnings_calendar: {
    label: "Earnings calendar (soon)",
    description: "Refresh the report date of holdings reporting soon, and of those with no date.",
    cron: "0 6 * * 0",
  },
  earnings_calendar_full: {
    label: "Earnings calendar (all)",
    description: "Refresh every holding's report date from its IR calendar.",
    cron: "0 5 1 1,4,7,10 *",
  },
  earnings_due: {
    label: "Earnings due",
    description: "Queue an earnings check for holdings that have reported.",
    cron: "0 8 * * *",
  },
  fact_reconcile: {
    label: "Fact reconciliation",
    description: "Check stored results figures against the issuers' 10-Q/10-K XBRL.",
    cron: "0 9 * * *",
  },
  reference_data: {
    label: "Reference data",
    description: "Identity, listings, legal entities and group structure (OpenFIGI, ESMA, GLEIF, Wikidata).",
    cron: "30 4 * * 1",
  },
  price_history: {
    label: "Price history",
    description: "Daily prices from Stooq and FX history after the close.",
    cron: "15 23 * * 1-5",
  },
  holiday_refresh: {
    label: "Holiday calendar",
    description: "Public holidays for this and next year (Nager.Date).",
    cron: "0 4 2 1,7 *",
  },
  news_ingest: {
    label: "News check",
    description: "Ingest, classify, corroborate and alert.",
    cron: "0 6-22/2 * * *",
  },
  daily_digest: {
    label: "Daily digest",
    description: "Digest of the last 24 hours.",
    cron: "0 7 * * 1-5",
  },
  weekly_digest: {
    label: "Weekly digest",
    description: "Digest of the last 7 days.",
    cron: "0 18 * * 0",
  },
};
export const SCHEDULE_KEYS = Object.keys(SCHEDULES) as ScheduleKey[];
export const DEFAULT_TIMEZONE = "Europe/Prague";

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_JOBS: JobsConfig = {
  schema_version: 1,
  ...(Object.fromEntries(
    SCHEDULE_KEYS.map((k) => [k, { enabled: true, cron: SCHEDULES[k].cron, timezone: DEFAULT_TIMEZONE }]),
  ) as Record<ScheduleKey, JobSchedule>),
  earnings_check: {
    extraction_model: "claude-sonnet-5",
    locate_model: "claude-sonnet-5",
    effort: "high",
    max_steps: 8,
    max_searches: 3,
    check_days_after_report: [1, 2, 4],
    max_document_mb: 20,
  },
  earnings_calendar_settings: { model: "claude-haiku-4-5", lookahead_days: 120, refresh_within_days: 14 },
  news: {
    classification_model: "claude-haiku-4-5",
    classification_batch_size: 50,
    corroboration_model: "claude-haiku-4-5",
    corroboration_max_searches: 3,
    corroboration_rounds: 2,
    recheck_hours: 48,
    max_items_per_run: 500,
  },
  digest: { model: "claude-sonnet-5", use_batch: true, batch_wait_minutes: 60, min_severity_summarised: 2 },
};

/** Model ids offered as suggestions; any `claude-…` id is accepted. */
export const MODEL_SUGGESTIONS = ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"];
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

export type SettingField<S extends SettingsKey> = {
  key: keyof Settings<S> & string;
  label: string;
  kind: "model" | "int" | "bool" | "effort" | "int-list" | "severity";
  hint?: string;
  min?: number;
  max?: number;
};

export const SETTINGS: { [S in SettingsKey]: { title: string; description: string; fields: SettingField<S>[] } } = {
  earnings_check: {
    title: "Earnings check",
    description: "Find the release, extract the figures, validate them and judge each KPI.",
    fields: [
      { key: "extraction_model", label: "Extraction model", kind: "model" },
      { key: "locate_model", label: "Locate model", kind: "model", hint: "Searches for the release when EDGAR has none." },
      { key: "effort", label: "Effort", kind: "effort" },
      { key: "max_steps", label: "Step cap per run", kind: "int", min: 2, max: 50, hint: "Claude calls plus document fetches." },
      { key: "max_searches", label: "Web searches per run", kind: "int", min: 0, max: 20 },
      { key: "check_days_after_report", label: "Days after the report date to check", kind: "int-list", hint: "Comma separated, e.g. 1, 2, 4 (retries until the release is found)." },
      { key: "max_document_mb", label: "Largest document (MB)", kind: "int", min: 1, max: 32 },
    ],
  },
  earnings_calendar_settings: {
    title: "Earnings calendar",
    description: "Next report dates: Finnhub for US issuers, the IR calendar page otherwise.",
    fields: [
      { key: "model", label: "Model", kind: "model", hint: "Reads report dates off IR calendar pages." },
      { key: "lookahead_days", label: "Look ahead (days)", kind: "int", min: 7, max: 400 },
      { key: "refresh_within_days", label: "Weekly run: holdings reporting within (days)", kind: "int", min: 1, max: 120, hint: "The quarterly run refreshes every holding." },
    ],
  },
  news: {
    title: "News pipeline",
    description: "Classification and corroboration after each news check.",
    fields: [
      { key: "classification_model", label: "Classification model", kind: "model" },
      { key: "classification_batch_size", label: "Items per classification call", kind: "int", min: 5, max: 100 },
      { key: "corroboration_model", label: "Corroboration model", kind: "model" },
      { key: "corroboration_max_searches", label: "Searches per corroboration call", kind: "int", min: 0, max: 10 },
      { key: "corroboration_rounds", label: "Corroboration rounds per story", kind: "int", min: 0, max: 5 },
      { key: "recheck_hours", label: "Re-check unconfirmed stories for (hours)", kind: "int", min: 1, max: 168 },
      { key: "max_items_per_run", label: "Classification cap per run", kind: "int", min: 10, max: 5000, hint: "Cost guard." },
    ],
  },
  digest: {
    title: "Digests",
    description: "How the daily and weekly digests are written.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      { key: "use_batch", label: "Write through the Batch API (half price)", kind: "bool" },
      { key: "batch_wait_minutes", label: "Wait for the batch (minutes)", kind: "int", min: 1, max: 720, hint: "Then the summary is written directly." },
      { key: "min_severity_summarised", label: "Summarise stories from severity", kind: "severity", hint: "Lower-severity stories are listed by headline." },
    ],
  },
};
export const SETTINGS_KEYS = Object.keys(SETTINGS) as SettingsKey[];

export type ScheduleForm = { name: string; enabled: boolean; cron: string; timezone: string; rest: Record<string, unknown> };
export type SettingsForm = { values: Record<string, string | boolean>; rest: Record<string, unknown> };
export type JobsForm = {
  schedules: ScheduleForm[];
  settings: Record<SettingsKey, SettingsForm>;
  /** Top-level properties this form does not know (kept as loaded). */
  rest: Record<string, unknown>;
};

function isSchedule(value: unknown): value is JobSchedule {
  return typeof value === "object" && value !== null && typeof (value as { cron?: unknown }).cron === "string";
}

export function scheduleLabel(name: string): string {
  const known = SCHEDULES[name as ScheduleKey];
  if (known) return known.label;
  const words = name.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function numberOrText(text: string): number | string {
  const trimmed = text.trim();
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : trimmed;
}

export function jobsFromBody(body: JobsConfig | null): JobsForm {
  const b = { ...DEFAULT_JOBS, ...(body ?? {}) } as Record<string, unknown>;
  const names = [...SCHEDULE_KEYS, ...Object.keys(b).filter((k) => !(k in SCHEDULES) && isSchedule(b[k]))];
  const schedules = names.map((name) => {
    const { enabled, cron, timezone, ...rest } = b[name] as JobSchedule;
    return { name, enabled: enabled ?? true, cron, timezone: timezone ?? DEFAULT_TIMEZONE, rest };
  });
  const settings = Object.fromEntries(
    SETTINGS_KEYS.map((s) => {
      const loaded = { ...(DEFAULT_JOBS[s] as Record<string, unknown>), ...((b[s] as Record<string, unknown>) ?? {}) };
      const values: Record<string, string | boolean> = {};
      const rest = { ...loaded };
      for (const field of SETTINGS[s].fields as SettingField<SettingsKey>[]) {
        const v = loaded[field.key];
        delete rest[field.key];
        values[field.key] =
          field.kind === "bool" ? Boolean(v) : Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v);
      }
      return [s, { values, rest }];
    }),
  ) as Record<SettingsKey, SettingsForm>;
  const known = new Set<string>(["schema_version", ...names, ...SETTINGS_KEYS]);
  const rest = Object.fromEntries(Object.entries(b).filter(([k]) => !known.has(k)));
  return { schedules, settings, rest };
}

export function jobsToBody(form: JobsForm): JobsConfig {
  const body: Record<string, unknown> = { ...form.rest, schema_version: 1 };
  for (const s of form.schedules) {
    body[s.name] = { ...s.rest, enabled: s.enabled, cron: s.cron.trim().replace(/\s+/g, " "), timezone: s.timezone.trim() };
  }
  for (const key of SETTINGS_KEYS) {
    const { values, rest } = form.settings[key];
    const out: Record<string, unknown> = { ...rest };
    for (const field of SETTINGS[key].fields as SettingField<SettingsKey>[]) {
      const v = values[field.key];
      if (field.kind === "bool") out[field.key] = Boolean(v);
      else if (field.kind === "int" || field.kind === "severity") out[field.key] = numberOrText(String(v));
      else if (field.kind === "int-list")
        out[field.key] = String(v)
          .split(/[\s,;]+/)
          .filter(Boolean)
          .map(numberOrText);
      else out[field.key] = String(v).trim();
    }
    body[key] = out;
  }
  return body as JobsConfig;
}
