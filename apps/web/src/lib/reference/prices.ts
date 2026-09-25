import type { PriceHistory, PricePoint } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";

/*
 * Display rules for the holding page's price and risk section. The risk figures are the
 * backend's stored calculation and are only formatted here, never recomputed; the only
 * arithmetic is rebasing the two chart series to 100 so they can share one axis.
 */

const EM_DASH = "—";

export const PRICE_RANGES = [
  { id: "3m", label: "3M", days: 91 },
  { id: "1y", label: "1Y", days: 365 },
  { id: "2y", label: "2Y", days: 730 },
  { id: "5y", label: "5Y", days: 1826 },
  { id: "max", label: "Max", days: 3700 },
] as const;
export type PriceRangeId = (typeof PRICE_RANGES)[number]["id"];
export const DEFAULT_RANGE: PriceRangeId = "1y";

export function rangeDays(id: PriceRangeId): number {
  return PRICE_RANGES.find((r) => r.id === id)?.days ?? 365;
}

/** The periods of the risk card, in the backend's keys (steward/reference/risk.py PERIODS). */
export const RETURN_PERIODS = [
  { key: "1m", label: "1 month" },
  { key: "3m", label: "3 months" },
  { key: "6m", label: "6 months" },
  { key: "1y", label: "1 year" },
  { key: "2y", label: "2 years" },
] as const;

export type ChartPoint = {
  date: string;
  /** Holding close rebased to 100 at `base`. */
  holding: number | null;
  /** Benchmark close rebased to 100 at `base`; null on days it has no close. */
  benchmark: number | null;
  holdingClose: number | null;
  benchmarkClose: number | null;
};

export type RebasedSeries = {
  points: ChartPoint[];
  /** The first date both series have a close (or the holding's first date without a benchmark). */
  base: string | null;
  /** True when the benchmark has a close on the base date, so it is drawn. */
  withBenchmark: boolean;
};

/**
 * Both series rebased to 100 at the first date on which both have a close. Without a
 * common date the holding alone is rebased at its own first close. Points start at the
 * base date and follow the holding's trading days.
 */
export function rebaseSeries(bars: PricePoint[], benchmark: PricePoint[]): RebasedSeries {
  const sorted = [...bars].sort((a, b) => a.date.localeCompare(b.date));
  if (sorted.length === 0) return { points: [], base: null, withBenchmark: false };
  const bench = new Map(benchmark.map((p) => [p.date, p.close]));
  const common = sorted.find((p) => bench.has(p.date) && p.close > 0 && (bench.get(p.date) ?? 0) > 0);
  const base = common ?? sorted.find((p) => p.close > 0);
  if (!base) return { points: [], base: null, withBenchmark: false };
  const holdingBase = base.close;
  const benchBase = common ? (bench.get(common.date) as number) : null;
  const points = sorted
    .filter((p) => p.date >= base.date)
    .map((p) => {
      const b = bench.get(p.date);
      return {
        date: p.date,
        holding: (p.close / holdingBase) * 100,
        benchmark: benchBase !== null && b !== undefined ? (b / benchBase) * 100 : null,
        holdingClose: p.close,
        benchmarkClose: b ?? null,
      };
    });
  return { points, base: base.date, withBenchmark: benchBase !== null };
}

/** A rebased index value: "112.3". */
export function formatIndex(value: number | null | undefined, locale?: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EM_DASH;
  return new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value);
}

/** A period return (fraction): "+12.3%". */
export function formatReturn(fraction: number | null | undefined, locale?: string): string {
  return formatPercent(fraction, { locale, decimals: 1, signed: true });
}

/** Annualised volatility or drawdown (fraction): "23.4%", "−31.2%". */
export function formatRiskPercent(fraction: number | null | undefined, locale?: string): string {
  return formatPercent(fraction, { locale, decimals: 1 });
}

/** Beta or correlation: two decimals, "0.87". */
export function formatCoefficient(value: number | null | undefined, locale?: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EM_DASH;
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "negative",
  }).format(value);
}

export type Plausibility = {
  date: string;
  stooqClose: number;
  trading212Price: number;
  /** Already a percentage (Stooq / Trading212 − 1, × 100), as the backend stores it. */
  differencePct: number;
  flagged: boolean;
};

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** The API types `plausibility` as a free-form object; read the fields the backend writes. */
export function parsePlausibility(raw: PriceHistory["plausibility"]): Plausibility | null {
  if (!raw || typeof raw !== "object") return null;
  const stooqClose = num(raw.stooq_close);
  const trading212Price = num(raw.trading212_price);
  const differencePct = num(raw.difference_pct);
  if (typeof raw.date !== "string" || stooqClose === null || trading212Price === null || differencePct === null) {
    return null;
  }
  return { date: raw.date, stooqClose, trading212Price, differencePct, flagged: raw.flagged === true };
}

/** "+0.44 %" from a value already in percent. */
export function formatDifferencePct(pct: number, locale?: string): string {
  const text = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
    signDisplay: "exceptZero",
  }).format(pct);
  return `${text} %`;
}

/** Correlations, highest first; holdings without a figure last. */
export function sortCorrelations<T extends { ticker: string; correlation: number | null }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    if (a.correlation === null && b.correlation === null) return a.ticker.localeCompare(b.ticker);
    if (a.correlation === null) return 1;
    if (b.correlation === null) return -1;
    return b.correlation - a.correlation;
  });
}
