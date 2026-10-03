import { formatCompact, formatNumber } from "@/lib/charts/format";

/** A filed value in its unit: per-share lines to two decimals, everything else compact. */
export function formatAmount(value: number, unit: string): string {
  return unit.includes("/") ? formatNumber(value, 2) : formatCompact(value);
}

/** "USD" or "USD/shares" -> a short unit caption for a chart. */
export function unitCaption(unit: string): string {
  return unit.includes("/") ? `${unit.replace("/shares", "")} per share` : unit;
}
