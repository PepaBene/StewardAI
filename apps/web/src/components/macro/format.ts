import { formatNumber } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { formatDate } from "@/lib/format";

/** A figure with digits that scale to its size, so 0.043 and 4,310 both keep their meaning. */
export function formatFigure(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return "n/a";
  const a = Math.abs(value);
  const digits = a === 0 ? 0 : a < 0.1 ? 4 : a < 10 ? 2 : a < 1000 ? 1 : 0;
  return formatNumber(value, digits);
}

/** Signed variant for changes: +0.3, −1.2; zero stays "0". */
export function formatChange(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return "n/a";
  const body = formatFigure(Math.abs(value));
  return value > 0 ? `+${body}` : value < 0 ? `−${body}` : body;
}

/** "27 Sep 2026" from a date or date-time string; the original text when it does not parse. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const d = iso.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? formatDate(d) : iso;
}
