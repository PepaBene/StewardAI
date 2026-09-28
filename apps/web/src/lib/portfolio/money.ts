import { formatCompact, MINUS } from "@/lib/charts/format";

/*
 * Compact money for tiles, legends and chart labels ("2.38M CZK", "+265.2k CZK"). Full
 * amounts use formatMoney from lib/format; this is only for places the design abbreviates.
 */

export function formatCompactMoney(value: number | null | undefined, currency: string, { signed = false } = {}): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const body = formatCompact(value);
  const sign = signed && value > 0 && body !== "0" ? "+" : "";
  return `${sign}${body} ${currency}`.trim();
}

/** A signed fraction as "+1.2%" / "−0.4%" with a true minus sign. */
export function formatSignedPct(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  const v = fraction * 100;
  const body = Math.abs(v).toFixed(digits);
  if (Number(body) === 0) return `${body}%`;
  return `${v > 0 ? "+" : MINUS}${body}%`;
}
