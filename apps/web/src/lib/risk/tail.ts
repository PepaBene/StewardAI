import type { components } from "@/lib/api/schema";

type S = components["schemas"];

export type TailReport = S["TailReport"];
export type TailLine = S["TailLine"];
export type DrawdownOdds = S["DrawdownOdds"];

/** "Over one month" / "Over one week" for a horizon in weeks. */
export function horizonText(weeks: number): string {
  if (weeks === 1) return "one week";
  if (weeks === 4) return "one month";
  return `${weeks} weeks`;
}

/** The worst of the methods at a horizon and level: the line to plan against. */
export function worstLine(lines: readonly TailLine[], weeks: number, level: number): TailLine | null {
  const same = lines.filter((l) => l.horizon_weeks === weeks && Math.abs(l.level - level) < 1e-9);
  if (same.length === 0) return null;
  return same.reduce((a, b) => (b.es > a.es ? b : a));
}

/** One sentence for the bad month: what the average loss in the worst one month in twenty is. */
export function badMonthSentence(lines: readonly TailLine[], currency: string | null | undefined): string | null {
  const line = worstLine(lines, 4, 0.95);
  if (!line) return null;
  const money = line.es_value != null ? `${Math.round(line.es_value).toLocaleString("en")} ${currency ?? ""}`.trim() : null;
  const pct = `${(line.es * 100).toFixed(1)} %`;
  return money
    ? `In the worst one month in twenty, the book has lost about ${pct} on average: ${money} at today's value.`
    : `In the worst one month in twenty, the book has lost about ${pct} on average.`;
}

/** Years as a label: "1 year", "3 years". */
export function yearsText(years: number): string {
  return years === 1 ? "1 year" : `${years} years`;
}
