import type { AlertOut, DigestBody, DigestSentence } from "@/lib/api/client";
import { digestOverviewPath, digestSectionPath } from "@/lib/feedback/feedback";

/*
 * The Reports page's derived figures: the portfolio index sparkline beside each report,
 * the digest hero (return over the digest's window, against the benchmark, alerts sent),
 * the "What happened" list and the three checks under it. Pure rules from data the API
 * already returns (`/portfolio/history`, the digest body, `/alerts`); nothing is invented.
 */

export type DatedClose = { date: string; close: number };

/** The calendar day (YYYY-MM-DD) of an instant in `timeZone` (default: the browser's). */
export function localDay(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
}

function sorted(points: readonly DatedClose[]): DatedClose[] {
  return points.filter((p) => Number.isFinite(p.close)).sort((a, b) => a.date.localeCompare(b.date));
}

/** The last `n` index values on or before `day`, oldest first (a report row's sparkline). */
export function historyUpTo(points: readonly DatedClose[], day: string, n = 30): number[] {
  const upTo = sorted(points).filter((p) => p.date <= day);
  return upTo.slice(Math.max(0, upTo.length - n)).map((p) => p.close);
}

export type WindowReturn = { pct: number; from: string; to: string };

/**
 * The move of a daily series across a window of days: from the last value before
 * `startDay` to the last value on or before `endDay`. Null when either side is missing or
 * both sides are the same point.
 */
export function windowReturn(points: readonly DatedClose[], startDay: string, endDay: string): WindowReturn | null {
  const s = sorted(points);
  const base = [...s].reverse().find((p) => p.date < startDay);
  const end = [...s].reverse().find((p) => p.date <= endDay);
  if (!base || !end || end.date <= base.date || base.close === 0) return null;
  return { pct: end.close / base.close - 1, from: base.date, to: end.date };
}

/** The first and last calendar days of a digest's news window. */
export function digestDays(body: Pick<DigestBody, "window_start" | "window_end">, timeZone?: string): { start: string; end: string } {
  return { start: localDay(body.window_start, timeZone), end: localDay(body.window_end, timeZone) };
}

// ---------------------------------------------------------------------------
// What happened
// ---------------------------------------------------------------------------

export type HappenedItem = {
  sentence: DigestSentence;
  /** Feedback path of the sentence (overview or section summary). */
  path: string;
  /** Holdings the sentence is about: its section's ticker, or every section's for the overview. */
  tickers: string[];
  /** Highest severity among the stories of its section(s); null when none. */
  severity: number | null;
  /** Readable context: the section name, or "Overview". */
  context: string;
};

/**
 * Every written sentence in reading order: the overview first, then each section's
 * summary. Overview sentences carry the tickers of the sections whose cited items they share.
 */
export function whatHappened(body: Pick<DigestBody, "overview" | "sections">): HappenedItem[] {
  const sections = body.sections ?? [];
  const maxSeverity = (secs: typeof sections): number | null => {
    const levels = secs.flatMap((s) => (s.stories ?? []).map((st) => st.severity));
    return levels.length ? Math.max(...levels) : null;
  };
  const overview = (body.overview ?? []).map((sentence, i): HappenedItem => {
    const ids = new Set(sentence.news_item_ids ?? []);
    const facts = new Set(sentence.fact_ids ?? []);
    const touched = sections.filter((sec) =>
      [...(sec.summary ?? [])].some(
        (s) => (s.news_item_ids ?? []).some((id) => ids.has(id)) || (s.fact_ids ?? []).some((id) => facts.has(id)),
      ) || (sec.stories ?? []).some((st) => st.news_item_ids.some((id) => ids.has(id))),
    );
    return {
      sentence,
      path: digestOverviewPath(i),
      tickers: touched.map((s) => s.ticker).filter((t): t is string => Boolean(t)),
      severity: maxSeverity(touched),
      context: "Overview",
    };
  });
  const bySection = sections.flatMap((sec, si) =>
    (sec.summary ?? []).map(
      (sentence, i): HappenedItem => ({
        sentence,
        path: digestSectionPath(si, i),
        tickers: sec.ticker ? [sec.ticker] : [],
        severity: maxSeverity([sec]),
        context: sec.name,
      }),
    ),
  );
  return [...overview, ...bySection];
}

// ---------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------

export type Ratio = { value: number; total: number };

export type DigestChecks = {
  /** Sentences the claim verifier found supported, of those it checked or kept. Null: not checked. */
  claims: Ratio | null;
  /** Cited news items from tier-1 sources, of all cited news items. */
  tier1: Ratio;
  /** Holdings with their own section in the digest, of the holdings held now. Null: holdings unknown. */
  holdings: Ratio | null;
};

export function digestChecks(
  body: Pick<DigestBody, "claims" | "sources" | "sections">,
  holdingTickers: readonly string[] | null,
): DigestChecks {
  const c = body.claims;
  const checked = c ? c.supported + c.unsupported + c.unchecked + c.removed : 0;
  const sources = body.sources ?? [];
  const covered = new Set((body.sections ?? []).map((s) => s.ticker).filter((t): t is string => Boolean(t)));
  return {
    claims: c && checked > 0 ? { value: c.supported, total: checked } : null,
    tier1: { value: sources.filter((s) => s.tier === 1).length, total: sources.length },
    holdings: holdingTickers ? { value: holdingTickers.filter((t) => covered.has(t)).length, total: holdingTickers.length } : null,
  };
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

/** Telegram alerts sent inside the digest's window, oldest first (their sent_at instants). */
export function telegramTimes(
  alerts: readonly Pick<AlertOut, "channel" | "status" | "sent_at">[],
  windowStart: string,
  windowEnd: string,
): string[] {
  const start = Date.parse(windowStart);
  const end = Date.parse(windowEnd);
  return alerts
    .filter((a) => a.channel === "telegram" && a.status === "sent" && a.sent_at)
    .map((a) => a.sent_at as string)
    .filter((t) => {
      const ms = Date.parse(t);
      return ms >= start && ms <= end;
    })
    .sort();
}
