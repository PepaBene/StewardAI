import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type PerformanceData = S["PerformanceResponse"];
export type WindowData = S["WindowOut"];
export type Figure = S["Figure"];
export type LedgerHealth = S["LedgerHealth"];
export type LotsData = S["LotsResponse"];
export type LotData = S["LotOut"];
export type AttributionData = S["AttributionResponse"];
export type SleevesData = S["SleevesResponse"];
export type SettingsData = S["SettingsResponse"];
export type Settings = S["PerformanceSettings"];
export type Point = S["PerformanceSeriesPoint"];

const WINDOW_NAMES: Record<string, string> = {
  "1M": "1 month",
  YTD: "Year to date",
  "1Y": "1 year",
  "3Y": "3 years",
  "5Y": "5 years",
  inception: "Since the first snapshot",
};

export function windowName(label: string): string {
  return WINDOW_NAMES[label] ?? label;
}

/** The first thing a figure says about why it is missing or qualified, or null. */
export function figureReason(figure: Figure | null | undefined): string | null {
  return figure?.warnings?.[0] ?? null;
}

/** "Passed", "5 months", "2 years 4 months" from a month count with one decimal. */
export function monthsRemainingLabel(months: number, passed: boolean): string {
  if (passed) return "Passed";
  const whole = Math.max(0, Math.round(months));
  if (whole < 1) return "Under a month";
  const years = Math.floor(whole / 12);
  const rest = whole % 12;
  const y = years === 1 ? "1 year" : `${years} years`;
  const m = rest === 1 ? "1 month" : `${rest} months`;
  if (years === 0) return m;
  return rest === 0 ? y : `${y} ${m}`;
}

function dayNumber(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`) / 86_400_000;
}

/** How far a lot is through its holding period, 0 to 1. */
export function holdingProgress(boughtOn: string, passesOn: string, asOf: string): number {
  const start = dayNumber(boughtOn);
  const end = dayNumber(passesOn);
  if (!(end > start)) return 1;
  return Math.min(1, Math.max(0, (dayNumber(asOf) - start) / (end - start)));
}

/** One plain sentence on whether the ledger can be trusted, for the strip above the figures. */
export function ledgerSentence(health: LedgerHealth): string {
  if (health.status === "empty") return "No transactions are stored yet, so returns cannot be checked against a ledger.";
  const parts: string[] = [];
  if (health.cash_breaks_total > 0) {
    parts.push(
      `${health.cash_breaks_total} ${health.cash_breaks_total === 1 ? "day" : "days"} where cash moved by an amount no stored transaction explains`,
    );
  }
  if (health.position_breaks.length > 0) {
    parts.push(
      `${health.position_breaks.length} ${health.position_breaks.length === 1 ? "holding" : "holdings"} the fills do not fully explain`,
    );
  }
  if (health.unsupported.length > 0) {
    parts.push(`${health.unsupported.length} transaction ${health.unsupported.length === 1 ? "type" : "types"} the ledger cannot read`);
  }
  if (health.needs_fx > 0) parts.push(`${health.needs_fx} in another currency`);
  if (parts.length === 0) return "Every cash change and held quantity is explained by the stored transactions.";
  return `The ledger has gaps: ${parts.join("; ")}.`;
}

export type Bar = { offset: number; width: number };

/** Horizontal bars around a shared zero line: offset and width as shares (0 to 1) of the track. */
export function barScale(values: number[]): (value: number) => Bar {
  const reach = Math.max(...values.map((v) => Math.abs(v)), 0);
  return (value: number) => {
    if (reach === 0) return { offset: 0.5, width: 0 };
    const half = (Math.abs(value) / reach) * 0.5;
    return value >= 0 ? { offset: 0.5, width: half } : { offset: 0.5 - half, width: half };
  };
}

export type Domain = { lo: number; hi: number };

export function seriesDomain(series: ReadonlyArray<ReadonlyArray<Point>>, include: number[] = []): Domain | null {
  const values = [...include, ...series.flatMap((s) => s.map((p) => p.value).filter((v): v is number => typeof v === "number"))];
  if (values.length === 0) return null;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.06;
  return { lo: lo - pad, hi: hi + pad };
}

/** An SVG path for a dated series, leaving a gap at any null value. Dates map by calendar day. */
export function seriesPath(
  points: ReadonlyArray<Point>,
  box: { left: number; right: number; top: number; bottom: number },
  span: { first: string; last: string },
  domain: Domain,
): string {
  const t0 = dayNumber(span.first);
  const t1 = dayNumber(span.last);
  const width = box.right - box.left;
  const height = box.bottom - box.top;
  let d = "";
  let pen = false;
  for (const p of points) {
    if (p.value === null || p.value === undefined) {
      pen = false;
      continue;
    }
    const x = box.left + (t1 === t0 ? 0 : ((dayNumber(p.date) - t0) / (t1 - t0)) * width);
    const y = box.top + (1 - (p.value - domain.lo) / (domain.hi - domain.lo)) * height;
    d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)} `;
    pen = true;
  }
  return d.trim();
}
