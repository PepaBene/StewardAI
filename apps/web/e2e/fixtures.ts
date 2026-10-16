/*
 * Real ids that fill the dynamic segments of the route walk. A route whose segment has no
 * fixture here (and no FIXTURE_<NAME> environment override) is skipped and listed as skipped in
 * the metrics file. Override on the command line to point at ids that exist in your data, e.g.
 * FIXTURE_ID=42 for the numeric `[id]` segments.
 */
export const FIXTURES: Record<string, string> = {
  ticker: "AAPL_US_EQ",
  isin: "IE00B5BMR087",
  cik: "1067983",
  slug: "dividend_income",
  domain: "strategy",
};

export function fixtureFor(segment: string): string | null {
  const override = process.env[`FIXTURE_${segment.toUpperCase().replaceAll("-", "_")}`];
  return override ?? FIXTURES[segment] ?? null;
}

/** The viewports every route is visited at. */
export const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const;
