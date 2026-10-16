/*
 * Pure helpers behind the insight components (UX v2 F18-F31, insight plan I0.7): score-mark
 * geometry, check ordering, forecast-bar totals, the month grid, stepped fair-value series and
 * the non-linear slider scale. The backend computes every figure; these only arrange them.
 */
import { annularSector, TAU } from "@/lib/charts/arc";
import type { components } from "@/lib/api/schema";
import { parseDay } from "@/lib/charts/time";

type Schemas = components["schemas"];
export type Check = Schemas["steward__checks__contract__Check"];
export type CheckState = Check["state"];
export type AxisScore = Schemas["AxisScore"];
export type Num = number | string | null | undefined;

export function num(v: Num): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

// --- Score mark ---------------------------------------------------------------------------

/** The five scored axes, clockwise from 12 o'clock. Order is fixed so every mark reads alike. */
export const MARK_AXES = ["value", "future", "past", "health", "dividend"] as const;
export type MarkAxis = (typeof MARK_AXES)[number];

export const AXIS_LABEL: Record<MarkAxis, string> = {
  value: "Value",
  future: "Future",
  past: "Past",
  health: "Health",
  dividend: "Dividend",
};

/** One petal: the axis, its 0..1 fill (null = unknown: drawn as an open gap, never as zero). */
export type MarkPetal = { axis: MarkAxis; fraction: number | null; notApplicable: boolean };

/**
 * Petals from axis scores. A score is on 0..max over applicable checks; an axis where every check
 * is "not applicable" (a non-payer's dividend axis) is flagged so it reads "n/a", not unknown.
 */
export function markPetals(axes: readonly Pick<AxisScore, "axis" | "score" | "max" | "applicable" | "na">[]): MarkPetal[] {
  return MARK_AXES.map((axis) => {
    const a = axes.find((x) => x.axis === axis);
    const score = num(a?.score);
    const max = a?.max ?? 6;
    const notApplicable = !!a && a.applicable === 0 && a.na > 0;
    return { axis, fraction: score === null || !(max > 0) ? null : Math.max(0, Math.min(1, score / max)), notApplicable };
  });
}

/** Petals from the compact `{axis: score}` map the marks endpoint and holdings rows carry. */
export function petalsFromMap(map: Record<string, Num> | null | undefined, max = 6): MarkPetal[] {
  return MARK_AXES.map((axis) => {
    const s = num(map?.[axis]);
    return { axis, fraction: s === null ? null : Math.max(0, Math.min(1, s / max)), notApplicable: false };
  });
}

/** The angular span of petal `i` of `n`, with a small gap so petals never touch. */
export function petalAngles(i: number, n = MARK_AXES.length, gap = 0.06): [number, number] {
  const span = TAU / n;
  const start = -Math.PI / 2 - span / 2 + i * span;
  return [start + gap, start + span - gap];
}

/** SVG path of a petal filled to `fraction` between the hub radius and the rim. */
export function petalPath(i: number, fraction: number, cx: number, cy: number, hub: number, rim: number): string {
  const [a0, a1] = petalAngles(i);
  const r = hub + (rim - hub) * Math.max(0, Math.min(1, fraction));
  return annularSector(cx, cy, hub, Math.max(hub + 0.01, r), a0, a1);
}

/** Mean of the known fractions on 0..6, or null when none is known. Used for screen aggregates. */
export function markMean(petals: readonly MarkPetal[]): number | null {
  const known = petals.map((p) => p.fraction).filter((f): f is number => f !== null);
  return known.length ? (known.reduce((s, f) => s + f, 0) / known.length) * 6 : null;
}

/** Score text: "4 / 6", "n/a" or "unknown". */
export function scoreText(p: Pick<AxisScore, "score" | "applicable" | "na" | "max">): string {
  const s = num(p.score);
  if (s !== null) return `${Number.isInteger(s) ? s : s.toFixed(1)} / ${p.max}`;
  return p.applicable === 0 && p.na > 0 ? "n/a" : "unknown";
}

/** "4 of 5 applicable checks pass", with unknowns named. */
export function axisCoverage(a: Pick<AxisScore, "passes" | "applicable" | "unknown" | "na">): string {
  if (a.applicable === 0 && a.unknown === 0) return "No check applies";
  const parts = [`${a.passes} of ${a.applicable} applicable pass`];
  if (a.unknown) parts.push(`${a.unknown} unknown`);
  if (a.na) parts.push(`${a.na} not applicable`);
  return parts.join(", ");
}

// --- Checks -------------------------------------------------------------------------------

const STATE_ORDER: Record<CheckState, number> = { fail: 0, unknown: 1, pass: 2, na: 3 };

/** Failed first, then unknown, passed, not applicable; stable within a state. */
export function sortChecks<T extends Pick<Check, "state">>(checks: readonly T[]): T[] {
  return checks
    .map((c, i) => ({ c, i }))
    .sort((a, b) => STATE_ORDER[a.c.state] - STATE_ORDER[b.c.state] || a.i - b.i)
    .map(({ c }) => c);
}

export const STATE_LABEL: Record<CheckState, string> = {
  pass: "Pass",
  fail: "Fail",
  na: "Not applicable",
  unknown: "Unknown",
};

export function stateCounts(checks: readonly Pick<Check, "state">[]): Record<CheckState, number> {
  const out: Record<CheckState, number> = { pass: 0, fail: 0, na: 0, unknown: 0 };
  for (const c of checks) out[c.state] += 1;
  return out;
}

// --- Forecast bars ------------------------------------------------------------------------

export type ForecastBar = {
  key: string;
  label: string;
  /** Received or declared (known) part, and the estimated part. */
  known: number;
  estimated: number;
  phase: "history" | "forecast" | "mixed";
  total: number;
};

/** Bars from the dividend months: received counts as known history, declared as known forecast. */
export function monthBars(months: readonly Schemas["MonthBar"][]): ForecastBar[] {
  return months.map((m) => {
    const d = parseDay(`${m.month}-01`);
    const known = (num(m.received) ?? 0) + (num(m.declared) ?? 0);
    const estimated = num(m.estimated) ?? 0;
    return {
      key: m.month,
      label: d ? `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.month]} ${String(d.year).slice(2)}` : m.month,
      known,
      estimated,
      phase: m.phase,
      total: known + estimated,
    };
  });
}

export function yearBars(years: readonly Schemas["YearBar"][]): ForecastBar[] {
  return years.map((y) => {
    const known = (num(y.received) ?? 0) + (num(y.declared) ?? 0);
    const estimated = num(y.estimated) ?? 0;
    return { key: String(y.year), label: String(y.year), known, estimated, phase: y.phase, total: known + estimated };
  });
}

/** Sum of bar totals in a phase; the adapter test checks it against the backend's summary. */
export function barTotal(bars: readonly ForecastBar[], phase?: ForecastBar["phase"]): number {
  return bars.filter((b) => !phase || b.phase === phase).reduce((s, b) => s + b.total, 0);
}

// --- Month calendar -----------------------------------------------------------------------

export type GridDay = { iso: string; day: number; inMonth: boolean };

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Weeks (Monday first) covering the month of `firstDay` ("2026-10-01"), with spill days. */
export function monthGrid(firstDay: string): GridDay[][] {
  const d = parseDay(firstDay);
  if (!d) return [];
  const start = new Date(Date.UTC(d.year, d.month, 1));
  const offset = (start.getUTCDay() + 6) % 7;
  const cursor = new Date(start);
  cursor.setUTCDate(1 - offset);
  const weeks: GridDay[][] = [];
  do {
    const week: GridDay[] = [];
    for (let k = 0; k < 7; k++) {
      week.push({
        iso: `${cursor.getUTCFullYear()}-${pad2(cursor.getUTCMonth() + 1)}-${pad2(cursor.getUTCDate())}`,
        day: cursor.getUTCDate(),
        inMonth: cursor.getUTCMonth() === d.month,
      });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    weeks.push(week);
  } while (cursor.getUTCMonth() === d.month);
  return weeks;
}

/** "2026-10" shifted by `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const d = parseDay(`${month}-01`);
  if (!d) return month;
  const t = d.year * 12 + d.month + delta;
  return `${Math.floor(t / 12)}-${pad2((t % 12) + 1)}`;
}

export function groupByDate<T extends { date: string }>(items: readonly T[]): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const it of items) {
    const list = out.get(it.date) ?? [];
    list.push(it);
    out.set(it.date, list);
  }
  return out;
}

// --- Step line ----------------------------------------------------------------------------

/**
 * For each date, the latest stepped value whose `as_of` is on or before it; null before the first
 * stored value (the line starts where the record starts, it is never back-filled).
 */
export function stepValues(dates: readonly string[], steps: readonly { as_of: string; value: Num }[]): (number | null)[] {
  const sorted = [...steps].sort((a, b) => a.as_of.localeCompare(b.as_of));
  let j = -1;
  return dates.map((date) => {
    while (j + 1 < sorted.length && sorted[j + 1].as_of <= date) j += 1;
    return j >= 0 ? num(sorted[j].value) : null;
  });
}

// --- Range slider -------------------------------------------------------------------------

/**
 * A piecewise-linear scale through `ticks` (strictly increasing values), so dense regions of a
 * metric get room: each gap between neighbouring ticks takes an equal share of the track.
 */
export function tickScale(ticks: readonly number[]) {
  const n = ticks.length - 1;
  const toPos = (v: number): number => {
    if (n < 1) return 0;
    if (v <= ticks[0]) return 0;
    if (v >= ticks[n]) return 1;
    let i = 0;
    while (i < n - 1 && v > ticks[i + 1]) i++;
    return (i + (v - ticks[i]) / (ticks[i + 1] - ticks[i])) / n;
  };
  const toValue = (p: number): number => {
    if (n < 1) return ticks[0] ?? 0;
    const x = Math.max(0, Math.min(1, p)) * n;
    const i = Math.min(n - 1, Math.floor(x));
    return ticks[i] + (x - i) * (ticks[i + 1] - ticks[i]);
  };
  return { toPos, toValue };
}

/** Round a slider value to a sensible step for its magnitude. */
export function niceRound(v: number): number {
  const a = Math.abs(v);
  const step = a >= 1000 ? 10 : a >= 100 ? 1 : a >= 10 ? 0.5 : a >= 1 ? 0.05 : 0.005;
  return Math.round(v / step) * step;
}
