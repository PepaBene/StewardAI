import type {
  ActivityCoverage,
  AlertKind,
  AlertOut,
  EvalCandidateOut,
  HoldingCoverage,
  JobListItem,
  JobStatus,
  SourceTrustOut,
} from "@/lib/api/client";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { durationBetween, formatDuration, jobTypeLabel } from "@/lib/jobs/job-detail";
import { eventLabel } from "@/lib/news/labels";
import { isTicker } from "@/lib/portfolio/transactions";

/*
 * The Activity page: the job log (`GET /jobs`), per-holding coverage (`GET
 * /activity/coverage`), monitoring alerts (`GET /alerts?kind=system`), source trust (`GET
 * /sources/trust`) and the evaluation candidates added from feedback (`GET
 * /eval-candidates`). Pure display and filter rules; the page only renders them.
 */

export const ACTIVITY_HREF = "/activity";

// ---------------------------------------------------------------------------
// Job log
// ---------------------------------------------------------------------------

/** Every job type the worker runs (apps/backend/src/steward/jobs/registry.py JOB_HANDLERS). */
export const JOB_TYPES = [
  "portfolio_sync",
  "news_ingest",
  "daily_digest",
  "weekly_digest",
  "event_escalation",
  "earnings_check",
  "earnings_due",
  "earnings_calendar",
  "earnings_calendar_full",
  "thesis_verdicts",
  "fact_reconcile",
  "reference_data",
  "price_history",
  "holiday_refresh",
  "macro_collect",
  "monitor",
  "research",
  "ownership_13f",
  "etf_look_through",
  "monthly_review",
  "chat",
  "sec_xbrl",
  "valuation_refresh",
] as const;

export const JOB_STATUSES = ["queued", "waiting", "running", "succeeded", "failed"] as const satisfies readonly JobStatus[];

export const JOB_PAGE = 50;

export type JobLogFilters = { types: string[]; statuses: JobStatus[]; ticker: string | null };

export const EMPTY_JOB_FILTERS: JobLogFilters = { types: [], statuses: [], ticker: null };

const TYPE = /^[a-z][a-z0-9_]{0,63}$/;

/** Filters from the page URL (?type=…&status=…&ticker=…); unknown values are dropped. */
export function parseJobFilters(params: URLSearchParams): JobLogFilters {
  const ticker = params.get("ticker");
  return {
    types: [...new Set(params.getAll("type").filter((t) => TYPE.test(t)))],
    statuses: JOB_STATUSES.filter((s) => params.getAll("status").includes(s)),
    ticker: ticker && isTicker(ticker) ? ticker : null,
  };
}

export function jobFiltersToSearch(filters: JobLogFilters, base?: URLSearchParams): string {
  const params = new URLSearchParams(base);
  params.delete("type");
  params.delete("status");
  params.delete("ticker");
  filters.types.forEach((t) => params.append("type", t));
  JOB_STATUSES.filter((s) => filters.statuses.includes(s)).forEach((s) => params.append("status", s));
  if (filters.ticker) params.set("ticker", filters.ticker);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function isJobFiltered(filters: JobLogFilters): boolean {
  return filters.types.length > 0 || filters.statuses.length > 0 || filters.ticker !== null;
}

/** The query of `GET /jobs` for these filters and page. */
export function jobLogQuery(filters: JobLogFilters, beforeId: number | null, limit = JOB_PAGE) {
  return {
    job_type: filters.types.length ? filters.types : undefined,
    status: filters.statuses.length ? filters.statuses : undefined,
    ticker: filters.ticker ?? undefined,
    before_id: beforeId ?? undefined,
    limit,
  };
}

/** Job types for the filter: the known ones, plus any seen in the log or the URL. */
export function jobTypeOptions(seen: readonly string[] = []): { value: string; label: string }[] {
  const all = [...new Set([...JOB_TYPES, ...seen])];
  return all.map((t) => ({ value: t, label: jobTypeLabel(t) })).sort((a, b) => a.label.localeCompare(b.label));
}

/** "ticker AAPL_US_EQ · kind daily": a run's parameters in one line (scalars only). */
export function paramsSummary(params: Record<string, unknown> | null | undefined): string | null {
  if (!params) return null;
  const parts = Object.entries(params)
    .filter(([, v]) => ["string", "number", "boolean"].includes(typeof v))
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${String(v)}`);
  return parts.length ? parts.join(" · ") : null;
}

/** How long a run took, or has been running, as text; "—" when it has not started. */
export function runDuration(job: Pick<JobListItem, "started_at" | "finished_at" | "status">, now: Date): string {
  const end = job.finished_at ?? (job.status === "running" ? now.toISOString() : null);
  const ms = durationBetween(job.started_at, end);
  return ms === null ? "—" : formatDuration(ms);
}

// ---------------------------------------------------------------------------
// Coverage
// ---------------------------------------------------------------------------

export type CoverageCell = { gap: boolean; label: string; detail: string };

export type CoverageRow = {
  ticker: string;
  name: string;
  news: CoverageCell;
  prices: CoverageCell;
  earnings: CoverageCell;
  gaps: number;
};

function windowText(hours: number): string {
  return hours % 24 === 0 && hours >= 48 ? `${hours / 24} days (${hours} h)` : `${hours} h`;
}

/**
 * One row per holding, gaps first: news coverage (a gap when no successful news run has
 * covered it within the monitoring window), price history (a gap when its latest bar is
 * stale) and the last earnings check. Every gap is spelled out, not coloured.
 */
export function coverageRows(coverage: ActivityCoverage, locale?: string): CoverageRow[] {
  const now = new Date(coverage.checked_at);
  const window = windowText(coverage.news_window_hours);
  const rows = coverage.holdings.map((h: HoldingCoverage): CoverageRow => {
    const lastNews = h.news_last_covered ? new Date(h.news_last_covered) : null;
    const news: CoverageCell = h.news_gap
      ? {
          gap: true,
          label: "Gap",
          detail: lastNews
            ? `No news run has covered it for ${window}: last covered ${formatDateTime(lastNews, { locale })} (${formatRelative(lastNews, now)}).`
            : `No news run has covered it within ${window}, nor in the 30 days before.`,
        }
      : lastNews
        ? { gap: false, label: formatRelative(lastNews, now), detail: `Last covered by a news run ${formatDateTime(lastNews, { locale })}.` }
        : { gap: false, label: "Not yet", detail: `Held for less than ${window}, or the news check is disabled.` };
    const prices: CoverageCell = !h.price_symbol
      ? { gap: false, label: "No symbol", detail: "No Stooq price symbol: no price history is collected." }
      : h.prices_gap
        ? {
            gap: true,
            label: "Gap",
            detail: h.prices_last_date
              ? `Latest ${h.price_symbol} bar is from ${formatDate(h.prices_last_date, locale)}: more than 4 days old.`
              : `No price bars for ${h.price_symbol}.`,
          }
        : {
            gap: false,
            label: h.prices_last_date ? formatDate(h.prices_last_date, locale) : "—",
            detail: `Latest ${h.price_symbol} bar${h.prices_last_date ? ` ${formatDate(h.prices_last_date, locale)}` : ""}.`,
          };
    const checked = h.earnings_last_check ? new Date(h.earnings_last_check) : null;
    const earnings: CoverageCell = checked
      ? {
          gap: h.earnings_last_status === "failed",
          label: `${h.earnings_last_status === "failed" ? "Failed" : "Checked"} ${formatRelative(checked, now)}`,
          detail: `Last earnings check ${h.earnings_last_status ?? "finished"} ${formatDateTime(checked, { locale })}.`,
        }
      : { gap: false, label: "Never", detail: "No earnings check has run for it yet." };
    return {
      ticker: h.ticker,
      name: h.name ?? h.ticker,
      news,
      prices,
      earnings,
      gaps: Number(news.gap) + Number(prices.gap),
    };
  });
  return rows.sort((a, b) => b.gaps - a.gaps || a.ticker.localeCompare(b.ticker));
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

export const ALERT_KIND_LABELS: Record<AlertKind, string> = {
  news: "News",
  thesis_breach: "Thesis breach",
  test: "Test",
  portfolio: "Portfolio",
  system: "Monitoring",
};

/** The monitor job's event names (apps/backend/src/steward/monitor.py). */
export const SYSTEM_EVENT_LABELS: Record<string, string> = {
  job_failed: "Failed job",
  missed_run: "Missed run",
  coverage_gap: "Coverage gap",
};

export function alertEventLabel(alert: Pick<AlertOut, "kind" | "event_type">): string {
  if (alert.kind === "system") return SYSTEM_EVENT_LABELS[alert.event_type ?? ""] ?? (alert.event_type ?? "Monitoring").replaceAll("_", " ");
  if (alert.kind === "news" || alert.kind === "test") return eventLabel(alert.event_type);
  return ALERT_KIND_LABELS[alert.kind];
}

/** "Sent 14:05", "Held until 07:00", "Suppressed: daily cap reached", "Failed: timeout". */
export function alertOutcome(alert: Pick<AlertOut, "status" | "sent_at" | "send_after" | "suppressed_reason" | "error">, locale?: string): string {
  switch (alert.status) {
    case "sent":
      return `Sent ${alert.sent_at ? formatDateTime(alert.sent_at, { locale }) : ""}`.trim();
    case "pending":
      return `Held until ${formatDateTime(alert.send_after, { locale })}`;
    case "suppressed":
      return `Suppressed${alert.suppressed_reason ? `: ${alert.suppressed_reason}` : ""}`;
    case "failed":
      return `Failed${alert.error ? `: ${alert.error}` : ""}`;
  }
}

/** The alert's message without its "[Severity n] StewardAI monitoring:" prefix, first line only. */
export function alertHeadline(message: string): string {
  const first = message.split("\n")[0] ?? "";
  return first.replace(/^\[Severity \d\]\s*/, "").replace(/^StewardAI monitoring:\s*/, "").trim() || message.trim();
}

// ---------------------------------------------------------------------------
// Source trust and evaluation candidates
// ---------------------------------------------------------------------------

/** Lowest trust first (the API's order), demoted sources marked. */
export function sortTrust(rows: SourceTrustOut[]): SourceTrustOut[] {
  return [...rows].sort((a, b) => a.score - b.score || a.host.localeCompare(b.host));
}

export function formatScore(score: number): string {
  return (Math.round(score * 100) / 100).toFixed(2);
}

export const EVAL_KIND_LABELS: Record<EvalCandidateOut["kind"], string> = {
  earnings_figure: "Golden set (earnings figure)",
  news_label: "Labelled set (news item)",
};

/** The item the candidate was made from, with its link. */
export function evalTarget(c: Pick<EvalCandidateOut, "fact_id" | "news_item_id">): { label: string; href: string | null } {
  if (c.fact_id !== null) return { label: `Figure #${c.fact_id}`, href: `/evidence/${c.fact_id}` };
  if (c.news_item_id !== null) return { label: `News item #${c.news_item_id}`, href: null };
  return { label: "Unknown item", href: null };
}

/** The corrected value or label, in words. */
export function describeCorrected(c: Pick<EvalCandidateOut, "kind" | "corrected">): string {
  const x = c.corrected ?? {};
  if (c.kind === "earnings_figure") {
    const parts = [`value ${String(x.value ?? "—")}`];
    if (x.value_as_written) parts.push(`as printed “${String(x.value_as_written)}”`);
    if (x.snippet) parts.push("with the passage stating it");
    return parts.join(", ");
  }
  const parts = [eventLabel(typeof x.event_type === "string" ? x.event_type : null)];
  parts.push(x.affected_ticker ? `affects ${String(x.affected_ticker)}` : "affects no holding");
  parts.push(x.relevant === false ? "not relevant" : "relevant");
  if (x.rumour === true) parts.push("rumour");
  return parts.join(" · ");
}

export const EXPORT_COMMAND = "python -m steward.verify.export_evals";
