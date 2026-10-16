/*
 * The macro dashboard's reading of a card (GET /macro/dashboard): how its value and changes are
 * written, how often it is published, and one plain sentence on where it stands against its own
 * history. Pure, so the wording is tested rather than eyeballed.
 */

import { formatCompact, formatNumber, formatSigned } from "@/lib/charts/format";

export type MacroRange = {
  years: number;
  low: number;
  high: number;
  percentile: number;
  readings: number;
};

export type Cadence =
  "daily" | "weekly" | "monthly" | "quarterly" | "irregular";

export const GEOGRAPHY_NAMES: Record<string, string> = {
  US: "United States",
  EA: "Euro area",
  EA21: "Euro area",
  DE: "Germany",
  CZ: "Czechia",
  JP: "Japan",
  WLD: "World prices",
  FX: "Currencies",
};

/** Filter chips, in reading order; a geography with no card is dropped by the page. */
export const GEOGRAPHY_ORDER = [
  "US",
  "EA",
  "DE",
  "CZ",
  "JP",
  "WLD",
  "FX",
] as const;

/** EA21 (Eurostat's euro area from 2026) and EA (the ECB's) are one place to a reader. */
export function geographyKey(geography: string): string {
  return geography === "EA21" ? "EA" : geography;
}

/** A rate or balance moves in percentage points; a level (price, index, count) in percent. */
export function changeIsPoints(unit: string): boolean {
  const u = unit.trim().toLowerCase();
  return u.includes("%") || u === "pp" || u === "thousands";
}

function digitsFor(value: number): number {
  const abs = Math.abs(value);
  if (abs >= 1000) return 0;
  if (abs >= 100) return 1;
  return 2;
}

/** The value as the card's headline: "5.19", "126.6", "13.5k", "199k". */
export function formatLevel(value: number, unit: string): string {
  if (Math.abs(value) >= 10000 && !changeIsPoints(unit))
    return formatCompact(value);
  return formatNumber(value, digitsFor(value));
}

/** The unit written after the value; a bare "%" sits on the number. */
export function unitSuffix(unit: string): string {
  const u = unit.trim();
  if (u === "%") return "%";
  if (u === "% p.a.") return "% a year";
  if (u === "pp") return " pp";
  if (u === "thousands") return "k jobs";
  return u ? ` ${u}` : "";
}

/** The change from `from` to `to`: "+0.36 pp" for a rate, "+2.1%" for a level, null if unknown. */
export function formatChange(
  from: number | null,
  to: number,
  unit: string,
): string | null {
  if (from === null || !Number.isFinite(from)) return null;
  if (changeIsPoints(unit)) {
    const diff = to - from;
    return unit.trim() === "thousands"
      ? `${formatSigned(diff, 0)}k`
      : `${formatSigned(diff, 2)} pp`;
  }
  if (from === 0) return null;
  return `${formatSigned(((to - from) / Math.abs(from)) * 100, 1)}%`;
}

/** How often the series is published, from the gaps between its last readings. */
export function cadenceOf(periods: readonly string[]): Cadence {
  if (periods.length < 3) return "irregular";
  const days = periods.slice(-6).map((p) => Date.parse(p));
  const gaps = days
    .slice(1)
    .map((d, i) => (d - days[i]) / 86_400_000)
    .sort((a, b) => a - b);
  const median = gaps[Math.floor(gaps.length / 2)];
  if (median <= 4) return "daily";
  if (median <= 10) return "weekly";
  if (median <= 45) return "monthly";
  if (median <= 100) return "quarterly";
  return "irregular";
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** The period a reading is for, as its cadence names it: "14 Oct", "Sep 2026", "Q2 2026". */
export function formatPeriod(period: string, cadence: Cadence): string {
  const [y, m, d] = period.split("-").map(Number);
  if (cadence === "daily" || cadence === "weekly")
    return `${d} ${MONTHS[m - 1]} ${y}`;
  if (cadence === "quarterly") return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
  return `${MONTHS[m - 1]} ${y}`;
}

/** "since Sep", "since the previous day" — the previous reading, in the cadence's words. */
export function previousLabel(cadence: Cadence): string {
  return {
    daily: "on the day",
    weekly: "on the week",
    monthly: "on the month",
    quarterly: "on the quarter",
    irregular: "since the last reading",
  }[cadence];
}

/**
 * Where the latest value sits, in words, using the longest window on record: "the highest in
 * 10 years", "low for the past 5 years", "mid-range over 1 year". Null with no window.
 */
export function standing(ranges: readonly MacroRange[]): string | null {
  const longest = [...ranges].sort((a, b) => b.years - a.years)[0];
  if (!longest) return null;
  const span = longest.years === 1 ? "1 year" : `${longest.years} years`;
  const p = longest.percentile;
  if (p >= 0.995) return `The highest in ${span}.`;
  if (p <= 1 / longest.readings + 0.005) return `The lowest in ${span}.`;
  if (p >= 0.85) return `High for the past ${span}.`;
  if (p <= 0.15) return `Low for the past ${span}.`;
  return `Mid-range over the past ${span}.`;
}

/** Position of `value` in [low, high] as 0–1 (0 when the range is flat). */
export function rangePosition(
  value: number,
  low: number,
  high: number,
): number {
  if (high <= low) return 0.5;
  return Math.min(1, Math.max(0, (value - low) / (high - low)));
}
