import { isFiniteNumber } from "@/lib/charts/scale";

import type {
  Bar,
  CorporateAction,
  ListingSeries,
  PriceDiagnostics,
  SessionStatus,
  VenueCalendar,
} from "./types";

// ---------------------------------------------------------------------------------------
// Quote units
// ---------------------------------------------------------------------------------------

/** Minor-unit currencies quoted in hundredths of the major unit. */
const MINOR_UNITS: Record<string, { major: string; divisor: number }> = {
  GBX: { major: "GBP", divisor: 100 },
  GBp: { major: "GBP", divisor: 100 },
  ZAc: { major: "ZAR", divisor: 100 },
};

export function majorUnit(quoteCurrency: string): { currency: string; divisor: number } {
  const m = MINOR_UNITS[quoteCurrency];
  return m ? { currency: m.major, divisor: m.divisor } : { currency: quoteCurrency, divisor: 1 };
}

/** "1,240.5 GBX (£12.41)": the quote as printed, plus the major-unit value when the quote is a minor unit. */
export function describeQuote(value: number, quoteCurrency: string): string {
  const { currency, divisor } = majorUnit(quoteCurrency);
  const printed = `${value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} ${quoteCurrency}`;
  if (divisor === 1) return printed;
  return `${printed} (${(value / divisor).toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} ${currency})`;
}

// ---------------------------------------------------------------------------------------
// Corporate-action markers
// ---------------------------------------------------------------------------------------

export type PlacedAction = {
  action: CorporateAction;
  /** Index of the first bar on or after the action date; null when the date is outside the series. */
  index: number | null;
  /** The action date is not itself a bar (weekend/holiday): it is drawn at the next session. */
  shifted: boolean;
};

/** Places each action on the first session at or after its date, flagging a shifted placement. */
export function placeActions(bars: readonly Pick<Bar, "date">[], actions: readonly CorporateAction[]): PlacedAction[] {
  if (bars.length === 0) return actions.map((action) => ({ action, index: null, shifted: false }));
  const first = bars[0].date;
  const last = bars[bars.length - 1].date;
  return actions.map((action) => {
    if (action.date < first || action.date > last) return { action, index: null, shifted: false };
    const index = bars.findIndex((b) => b.date >= action.date);
    return { action, index, shifted: index >= 0 && bars[index].date !== action.date };
  });
}

/**
 * Close-to-close moves beyond `threshold` that no recorded action explains. A warning that a
 * series may be missing an action or contain a bad bar: never evidence that an action
 * happened, and never used to create one.
 */
export function unexplainedJumps(
  bars: readonly Bar[],
  actions: readonly CorporateAction[],
  threshold = 0.3,
): { date: string; change: number }[] {
  const explained = new Set(actions.map((a) => a.date));
  const out: { date: string; change: number }[] = [];
  for (let i = 1; i < bars.length; i++) {
    const prev = bars[i - 1].close;
    if (!(prev > 0)) continue;
    const change = bars[i].close / prev - 1;
    if (Math.abs(change) > threshold && !explained.has(bars[i].date)) out.push({ date: bars[i].date, change });
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// Series basis
// ---------------------------------------------------------------------------------------

export type BasisChoice = { basis: "raw" | "adjusted"; bars: readonly Bar[]; available: boolean; reason: string | null };

/**
 * The bars for a requested basis. Adjusted is available only with a reconciled adjusted
 * series; a differently-adjusted series is never substituted under the same label.
 */
export function barsForBasis(listing: ListingSeries, basis: "raw" | "adjusted"): BasisChoice {
  if (basis === "raw") return { basis, bars: listing.raw, available: true, reason: null };
  if (!listing.adjusted) return { basis, bars: [], available: false, reason: "No adjusted series exists for this listing." };
  if (!listing.adjustmentReconciled) {
    return { basis, bars: [], available: false, reason: "The adjusted series has not been reconciled against the confirmed corporate actions." };
  }
  return { basis, bars: listing.adjusted, available: true, reason: null };
}

/** Why total return cannot be shown, from the actions themselves: a confirmed dividend with no amount, or an unconfirmed action. */
export function totalReturnBlockers(actions: readonly CorporateAction[]): string[] {
  const out: string[] = [];
  for (const a of actions) {
    if (a.type === "cash_dividend" || a.type === "stock_dividend") {
      if (a.detail === null) out.push(`${a.date}: dividend amount is not recorded`);
      else if (!a.confirmed) out.push(`${a.date}: dividend is not confirmed by an issuer record`);
    }
    if (a.type === "split" && (a.factor === null || !a.confirmed)) out.push(`${a.date}: split is not confirmed`);
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// Event study
// ---------------------------------------------------------------------------------------

export type EventWindow = { eventDate: string; label: string; points: { offset: number; value: number }[] };

/**
 * Indexed closes (100 at offset −1, the last session before the event) for sessions
 * −`before`..+`after` around each event date. Events whose window runs off the series are
 * omitted rather than padded.
 */
export function eventWindows(
  bars: readonly Pick<Bar, "date" | "close">[],
  events: readonly { date: string; label: string }[],
  before: number,
  after: number,
): EventWindow[] {
  const out: EventWindow[] = [];
  for (const e of events) {
    const idx = bars.findIndex((b) => b.date >= e.date);
    if (idx < 1 || idx - before < 0 || idx + after >= bars.length) continue;
    const base = bars[idx - 1].close;
    if (!(base > 0)) continue;
    const points: { offset: number; value: number }[] = [];
    for (let k = -before; k <= after; k++) {
      // Offset −1 is the base session; offset 0 is the event session itself.
      points.push({ offset: k, value: (bars[idx + k].close / base) * 100 });
    }
    out.push({ eventDate: e.date, label: e.label, points });
  }
  return out;
}

/** Mean indexed path across windows of identical shape; null with none. */
export function meanWindow(windows: readonly EventWindow[]): { offset: number; value: number }[] | null {
  if (windows.length === 0) return null;
  return windows[0].points.map((p, i) => ({ offset: p.offset, value: windows.reduce((s, w) => s + w.points[i].value, 0) / windows.length }));
}

export type EventStudyGate = { allowed: true } | { allowed: false; reason: string };

/**
 * An event study is only meaningful once adjusted and raw series are reconciled: before that
 * a split would show up as a −75% "event reaction". Without a reconciled adjustment the
 * panel is replaced by the reason.
 */
export function eventStudyGate(listing: ListingSeries): EventStudyGate {
  const choice = barsForBasis(listing, "adjusted");
  if (!choice.available) return { allowed: false, reason: choice.reason ?? "No reconciled adjusted series." };
  const blockers = listing.actions.filter((a) => a.type === "split" && !a.confirmed);
  if (blockers.length > 0) return { allowed: false, reason: `${blockers.length} split${blockers.length === 1 ? "" : "s"} unconfirmed: the adjustment cannot be trusted yet.` };
  return { allowed: true };
}

// ---------------------------------------------------------------------------------------
// Correlation
// ---------------------------------------------------------------------------------------

/** Simple returns from closes; the first element has no predecessor and is null. */
export function simpleReturns(closes: readonly (number | null)[]): (number | null)[] {
  return closes.map((c, i) => {
    const prev = i > 0 ? closes[i - 1] : null;
    return isFiniteNumber(c) && isFiniteNumber(prev) && prev > 0 ? c / prev - 1 : null;
  });
}

/** Pearson correlation over the positions where both series have a value; null below `minShared`. */
export function pairCorrelation(
  a: readonly (number | null)[],
  b: readonly (number | null)[],
  minShared = 20,
): { r: number | null; shared: number } {
  const xs: number[] = [];
  const ys: number[] = [];
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (isFiniteNumber(a[i]) && isFiniteNumber(b[i])) {
      xs.push(a[i]!);
      ys.push(b[i]!);
    }
  }
  const shared = xs.length;
  if (shared < Math.max(3, minShared)) return { r: null, shared };
  const mx = xs.reduce((s, v) => s + v, 0) / shared;
  const my = ys.reduce((s, v) => s + v, 0) / shared;
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < shared; i++) {
    const dx = xs[i] - mx;
    const dy = ys[i] - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  if (sxx === 0 || syy === 0) return { r: null, shared };
  return { r: Math.max(-1, Math.min(1, sxy / Math.sqrt(sxx * syy))), shared };
}

export function correlationMatrix(
  series: readonly (readonly (number | null)[])[],
  minShared = 20,
): { matrix: (number | null)[][]; counts: number[][] } {
  const n = series.length;
  const matrix: (number | null)[][] = Array.from({ length: n }, () => Array<number | null>(n).fill(null));
  const counts: number[][] = Array.from({ length: n }, () => Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      const { r, shared } = i === j ? { r: 1, shared: series[i].filter(isFiniteNumber).length } : pairCorrelation(series[i], series[j], minShared);
      matrix[i][j] = matrix[j][i] = r;
      counts[i][j] = counts[j][i] = shared;
    }
  }
  return { matrix, counts };
}

/**
 * Leaf order of an average-linkage agglomerative clustering on distance 1 − r, so correlated
 * listings sit next to each other. A missing correlation counts as distance 1 (unrelated),
 * never as 0. Deterministic: ties break toward the lower index.
 */
export function clusterOrder(matrix: readonly (readonly (number | null)[])[]): number[] {
  const n = matrix.length;
  if (n <= 2) return Array.from({ length: n }, (_, i) => i);
  const dist = (i: number, j: number) => (isFiniteNumber(matrix[i][j]) ? 1 - matrix[i][j]! : 1);
  let clusters: { members: number[] }[] = Array.from({ length: n }, (_, i) => ({ members: [i] }));
  const avg = (a: number[], b: number[]) => {
    let s = 0;
    for (const i of a) for (const j of b) s += dist(i, j);
    return s / (a.length * b.length);
  };
  while (clusters.length > 1) {
    let best: [number, number] = [0, 1];
    let bestD = Infinity;
    for (let i = 0; i < clusters.length; i++) {
      for (let j = i + 1; j < clusters.length; j++) {
        const d = avg(clusters[i].members, clusters[j].members);
        if (d < bestD - 1e-12) {
          bestD = d;
          best = [i, j];
        }
      }
    }
    const [i, j] = best;
    const merged = { members: [...clusters[i].members, ...clusters[j].members] };
    clusters = clusters.filter((_, k) => k !== i && k !== j);
    clusters.push(merged);
  }
  return clusters[0].members;
}

/** Smallest and median shared-observation count off the diagonal, for the caption. */
export function sharedObservationStats(counts: readonly (readonly number[])[]): { min: number; median: number } | null {
  const vals: number[] = [];
  for (let i = 0; i < counts.length; i++) for (let j = i + 1; j < counts.length; j++) vals.push(counts[i][j]);
  if (vals.length === 0) return null;
  vals.sort((a, b) => a - b);
  const mid = Math.floor(vals.length / 2);
  return { min: vals[0], median: vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2 };
}

// ---------------------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------------------

export type SessionCell = { date: string; status: SessionStatus; note: string | null };

/**
 * Per weekday for a listing: what the venue did, combined with whether we hold a bar. A
 * trading day with no bar is `missing_bar` (a genuine gap); a closure or half-day is explained
 * by the venue calendar; a national holiday on a day the venue was open is `national_holiday_open`
 * (the holiday does not excuse a missing bar there).
 */
export function classifySessions(calendar: VenueCalendar, barDates: Iterable<string>): SessionCell[] {
  const have = new Set(barDates);
  return calendar.days.map((d): SessionCell => {
    if (d.kind === "closed") {
      return d.nationalHoliday
        ? { date: d.date, status: "exchange_holiday", note: d.reason ?? d.nationalHoliday }
        : { date: d.date, status: "exchange_closure", note: d.reason ?? "Ad hoc closure" };
    }
    if (!have.has(d.date)) return { date: d.date, status: "missing_bar", note: "Venue open, no bar held" };
    if (d.kind === "half_day") return { date: d.date, status: "half_day", note: d.reason };
    return d.nationalHoliday
      ? { date: d.date, status: "national_holiday_open", note: `${d.nationalHoliday}: venue open` }
      : { date: d.date, status: "trading", note: null };
  });
}

export function sessionCounts(cells: readonly SessionCell[]): Record<SessionStatus, number> {
  const c: Record<SessionStatus, number> = {
    trading: 0,
    half_day: 0,
    exchange_holiday: 0,
    national_holiday_open: 0,
    exchange_closure: 0,
    missing_bar: 0,
  };
  for (const cell of cells) c[cell.status] += 1;
  return c;
}

/** Share of the days the venue was open on which we hold a bar; null when it was never open. */
export function completeness(cells: readonly SessionCell[]): number | null {
  const c = sessionCounts(cells);
  const expected = c.trading + c.half_day + c.national_holiday_open + c.missing_bar;
  return expected > 0 ? (expected - c.missing_bar) / expected : null;
}

// ---------------------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------------------

export function disagreementPct(d: { primary: number; other: number }): number | null {
  return d.primary > 0 ? d.other / d.primary - 1 : null;
}

/** A disagreement that looks like a unit error (≈100× or ≈1/100) rather than a price difference. */
export function looksLikeUnitError(d: { primary: number; other: number }): boolean {
  if (!(d.primary > 0) || !(d.other > 0)) return false;
  const ratio = d.other / d.primary;
  return Math.abs(Math.log10(ratio) - Math.round(Math.log10(ratio))) < 0.02 && Math.abs(Math.round(Math.log10(ratio))) >= 1;
}

export function diagnosticsHeadline(d: PriceDiagnostics, completenessShare: number | null): string {
  const parts = [`Source of record ${d.sourceOfRecord}`, `quoted in ${d.quoteCurrency} on ${d.exchange}`];
  if (d.disagreements.length > 0) parts.push(`${d.disagreements.length} disagreement${d.disagreements.length === 1 ? "" : "s"} with alternatives`);
  if (completenessShare !== null) parts.push(`${Math.round(completenessShare * 100)}% of open sessions held`);
  return parts.join(" · ");
}
