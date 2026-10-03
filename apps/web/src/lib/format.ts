/*
 * Number and time formatting for every figure the app shows. Render the result in an
 * element with the `num` utility (tabular numerals) and right-align numeric columns.
 *
 * `locale` defaults to the browser's; tests pass an explicit one.
 */

type Locale = string | undefined;

const EM_DASH = "—";

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 1234.5, "CZK" -> "CZK 1,234.50". Unknown codes (e.g. GBX) still format. */
export function formatMoney(
  value: number | null | undefined,
  currency: string,
  { locale, decimals = 2, signed = false }: { locale?: Locale; decimals?: number; signed?: boolean } = {},
): string {
  if (!isFiniteNumber(value)) return EM_DASH;
  // A float residual (-8.9e-14 from a subtraction that should be 0) rounds to zero at this
  // precision and must read "0", not "-0".
  const shown = Math.abs(value) < 0.5 * 10 ** -decimals ? 0 : value;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(shown);
}

/** A price in its own currency, with up to 4 decimals (some quotes need them). */
export function formatPrice(value: number | null | undefined, currency: string, locale?: Locale) {
  if (!isFiniteNumber(value)) return EM_DASH;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(value);
}

/** Share quantities: up to 6 decimals, trailing zeros trimmed (fractional shares). */
export function formatQuantity(value: number | null | undefined, locale?: Locale) {
  if (!isFiniteNumber(value)) return EM_DASH;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(value);
}

/** A fraction as a percentage: 0.1234 -> "12.3%". */
export function formatPercent(
  fraction: number | null | undefined,
  { locale, decimals = 1, signed = false }: { locale?: Locale; decimals?: number; signed?: boolean } = {},
) {
  if (!isFiniteNumber(fraction)) return EM_DASH;
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(fraction);
}

/** A difference of two weights in percentage points: 0.012 -> "+1.2 pp". */
export function formatPp(fraction: number | null | undefined, locale?: Locale) {
  if (!isFiniteNumber(fraction)) return EM_DASH;
  const points = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  }).format(fraction * 100);
  return `${points} pp`;
}

/** Wall-clock time, "22:31". */
export function formatTime(date: Date, locale?: Locale) {
  return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

/** "25 Sep 2026". */
export function formatDate(date: Date | string, locale?: Locale) {
  const d = typeof date === "string" ? new Date(`${date}T00:00:00`) : date;
  return d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

/** "25 Sep 2026, 14:05". */
export function formatDateTime(
  date: Date | string,
  { locale, timeZone }: { locale?: Locale; timeZone?: string } = {},
) {
  const d = typeof date === "string" ? new Date(date) : date;
  if (Number.isNaN(d.getTime())) return EM_DASH;
  return d.toLocaleString(locale, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}

/** "just now", "4 min ago", "3 h ago", "2 days ago". */
export function formatRelative(date: Date, now: Date = new Date()) {
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** Tone for a signed financial figure: gain/loss colours are for P/L only. */
export function gainLossTone(value: number | null | undefined): "gain" | "loss" | "flat" {
  if (!isFiniteNumber(value) || value === 0) return "flat";
  return value > 0 ? "gain" : "loss";
}
