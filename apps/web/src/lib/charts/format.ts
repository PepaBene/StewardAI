/*
 * Chart label formatting. A fixed locale keeps server and client renders identical (no
 * hydration mismatch); components take a `format*` prop when a page needs another style.
 * Negative numbers use a true minus sign (U+2212), as in the design.
 */

const LOCALE = "en-US";
export const MINUS = "−";

function fixed(value: number, digits: number): string {
  return new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Math.abs(value));
}

/** 1234.5 -> "1,234.5"; -3 -> "−3". */
export function formatNumber(value: number, digits = 0): string {
  const body = fixed(value, digits);
  return value < 0 && body !== fixed(0, digits) ? MINUS + body : body;
}

/** Signed: 1.8 -> "+1.8", -0.6 -> "−0.6", 0 -> "0.0". */
export function formatSigned(value: number, digits = 1): string {
  const body = fixed(value, digits);
  if (body === fixed(0, digits)) return body;
  return (value > 0 ? "+" : MINUS) + body;
}

/** A fraction as a percentage: 0.087 -> "8.7%" (or "+8.7%" when signed). */
export function formatPct(fraction: number, digits = 1, signed = false): string {
  const pct = fraction * 100;
  return (signed ? formatSigned(pct, digits) : formatNumber(pct, digits)) + "%";
}

/** Compact magnitude: 2_486_300 -> "2.49M", 104_400 -> "104.4k". */
export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? MINUS : "";
  if (abs >= 1e9) return sign + fixed(abs / 1e9, 2) + "B";
  if (abs >= 1e6) return sign + fixed(abs / 1e6, 2) + "M";
  if (abs >= 1e3) return sign + fixed(abs / 1e3, 1) + "k";
  return sign + fixed(abs, abs < 10 && abs % 1 !== 0 ? 1 : 0);
}
