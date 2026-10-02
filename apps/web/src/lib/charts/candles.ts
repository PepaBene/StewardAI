/*
 * OHLC helpers for the candle chart.
 */

import { bandScale, isFiniteNumber } from "./scale";

/** Right-hand gutter of the candle chart (y labels), shared with charts aligned to it. */
export const CANDLE_RIGHT = 52;

/** The horizontal band layout of the candle chart for `count` bars in a `width` viewBox. */
export function candleBand(count: number, width: number) {
  return bandScale(count, [0, width - CANDLE_RIGHT], { paddingInner: 0.36 });
}

/** One trading day. open/high/low may be missing when the source only has closes. */
export type OhlcBar = {
  date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close: number | null;
  volume?: number | null;
};

/** A bar with every OHLC field present and internally consistent (low ≤ open, close ≤ high). */
export function isCompleteBar(bar: OhlcBar): boolean {
  const { open, high, low, close } = bar;
  if (!isFiniteNumber(open) || !isFiniteNumber(high) || !isFiniteNumber(low) || !isFiniteNumber(close)) {
    return false;
  }
  return low <= Math.min(open, close) && high >= Math.max(open, close);
}

/**
 * True when candles can be drawn honestly: every bar that has a close also has full,
 * consistent OHLC. One gap makes the whole series fall back to a close line (mixing
 * candles with invented bodies would misstate the missing days).
 */
export function hasCompleteOhlc(bars: readonly OhlcBar[]): boolean {
  const priced = bars.filter((b) => isFiniteNumber(b.close));
  return priced.length > 0 && priced.every(isCompleteBar);
}

/** True when at least one bar carries a positive volume. */
export function hasVolume(bars: readonly OhlcBar[]): boolean {
  return bars.some((b) => isFiniteNumber(b.volume) && b.volume > 0);
}
