import { formatCompact, formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";

/** Fraction -> "12.3%", with a dash for a missing value (never "0%"). */
export function pct(fraction: number | null | undefined, digits = 1): string {
  return fraction === null || fraction === undefined || !Number.isFinite(fraction) ? "—" : formatPct(fraction, digits);
}

/** Small weights keep two decimals so a 0.04% position does not read as 0%. */
export function weightPct(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  return formatPct(fraction, Math.abs(fraction) < 0.1 ? 2 : 1);
}

export function date(value: string | null | undefined): string {
  return value ? formatDate(value.slice(0, 10)) : "—";
}

export function compact(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : formatCompact(value);
}

export function signedInt(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : formatSigned(value, 0);
}

export function num(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : formatNumber(value, digits);
}

export function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(1, max - 1))}…`;
}
