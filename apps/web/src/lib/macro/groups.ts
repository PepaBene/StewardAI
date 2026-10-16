/*
 * Macro small multiples are grouped by what a series measures (plan §5.10, spec C.5), read
 * from its series id. Order is fixed so a group never moves when another empties.
 */

export const MACRO_GROUPS = ["Rates", "Inflation", "Growth and labour", "Credit and money", "Commodities", "Currencies", "Other"] as const;

export type MacroGroup = (typeof MACRO_GROUPS)[number];

const RULES: readonly [MacroGroup, RegExp][] = [
  ["Commodities", /commodit|copper|alumin|nickel|oil|brent|wti|gold|silver|gas\b|wheat/],
  ["Currencies", /\bfx\b|fx_|_fx|eur_?usd|usd_?|exchange|currency/],
  ["Inflation", /cpi|hicp|inflation|ppi|pce/],
  ["Credit and money", /spread|credit|m3|m2|loan|vix|volatil/],
  ["Rates", /rate|yield|10y|2y|bond|funds|treasury|bund|czeonia|repo/],
  ["Growth and labour", /gdp|unemploy|payroll|employment|claims|pmi|retail|industrial|production|confidence|sentiment|housing/],
];

/** The group a series id belongs to; unmatched ids fall into "Other". */
export function macroGroup(seriesId: string): MacroGroup {
  const id = seriesId.toLowerCase();
  for (const [group, re] of RULES) if (re.test(id)) return group;
  return "Other";
}

/** Items bucketed by group, in MACRO_GROUPS order, empty groups dropped; item order kept. */
export function groupMacro<T>(items: readonly T[], seriesId: (item: T) => string): { group: MacroGroup; items: T[] }[] {
  const buckets = new Map<MacroGroup, T[]>();
  for (const item of items) {
    const g = macroGroup(seriesId(item));
    buckets.set(g, [...(buckets.get(g) ?? []), item]);
  }
  return MACRO_GROUPS.filter((g) => buckets.has(g)).map((group) => ({ group, items: buckets.get(group)! }));
}
