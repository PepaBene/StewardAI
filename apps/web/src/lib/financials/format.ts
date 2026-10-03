import { formatCompact, formatNumber } from "@/lib/charts/format";

/**
 * A filed value in its unit: per-share lines to two decimals, pure numbers (tax rates and other
 * ratios, which are fractions such as 0.156) to three, everything else compact. Compact notation
 * would round 0.156 to "0.2".
 */
export function formatAmount(value: number, unit: string): string {
  if (unit === "pure") return formatNumber(value, 3);
  return unit.includes("/") ? formatNumber(value, 2) : formatCompact(value);
}

/** "USD" or "USD/shares" -> a short unit caption for a chart. */
export function unitCaption(unit: string): string {
  return unit.includes("/") ? `${unit.replace("/shares", "")} per share` : unit;
}
