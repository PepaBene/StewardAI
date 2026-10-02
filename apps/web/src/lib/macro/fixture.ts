import { gaussian, monthEnd, seeded } from "@/lib/fixtures/prng";

import type {
  CountryComparison,
  HoldingExposureMap,
  MacroObservation,
  MacroSeries,
  TradeFlow,
  TradeSlice,
} from "./types";

/*
 * Illustrative fixtures for the Phase 2 views. Series are synthetic random walks around a
 * plausible level; they are not published statistics. Provider names and update frequencies
 * follow the plan; first-release gaps and revisions are built in so every state is visible.
 */

const r2 = (v: number) => Math.round(v * 100) / 100;

function walk(opts: {
  seed: number;
  periods: string[];
  start: number;
  drift?: number;
  vol: number;
  /** Share of observations revised after first release. */
  revisedShare?: number;
  /** First release was not archived before this period (vintage_unavailable). */
  archivedFrom?: string;
}): MacroObservation[] {
  const rand = seeded(opts.seed);
  let level = opts.start;
  return opts.periods.map((period) => {
    level += (opts.drift ?? 0) + gaussian(rand) * opts.vol;
    const latest = r2(level);
    const archived = !opts.archivedFrom || period >= opts.archivedFrom;
    const revised = archived && rand() < (opts.revisedShare ?? 0);
    const firstRelease = archived ? (revised ? r2(latest - (rand() - 0.4) * opts.vol * 1.6) : latest) : null;
    return {
      period,
      latest,
      firstRelease,
      releasedAt: `${period.slice(0, 7)}-28T09:00:00Z`,
      revisedAt: revised ? `${period.slice(0, 4)}-12-15T09:00:00Z` : null,
    };
  });
}

const months = (fromYear: number, fromMonth: number, toYear: number, toMonth: number) => {
  const out: string[] = [];
  for (let y = fromYear, m = fromMonth; y < toYear || (y === toYear && m <= toMonth); m++) {
    if (m > 12) {
      m = 1;
      y++;
      if (y > toYear) break;
    }
    out.push(monthEnd(y, m));
  }
  return out;
};
const quarters = (fromYear: number, toYear: number, lastQuarter: number) =>
  Array.from({ length: (toYear - fromYear) * 4 + lastQuarter }, (_, i) => monthEnd(fromYear + Math.floor(i / 4), ((i % 4) + 1) * 3));
const years = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => `${from + i}-12-31`);

export const MACRO_SERIES_FIXTURE: readonly MacroSeries[] = [
  {
    id: "wb:DEU:gdp_growth",
    title: "Real GDP growth",
    provider: "World Bank",
    country: "DEU",
    countryName: "Germany",
    unit: "% year on year",
    frequency: "annual",
    seasonalAdjustment: "not applicable",
    sourceUrl: "https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG",
    observations: walk({ seed: 11, periods: years(2012, 2025), start: 1.4, vol: 1.1, revisedShare: 0.3, archivedFrom: "2019-12-31" }),
    nextRelease: "2027-07-01",
    state: "stale",
    reason: "Annual indicator: the 2026 value is not published before mid-2027.",
  },
  {
    id: "oecd:DEU:cpi",
    title: "Consumer price inflation",
    provider: "OECD",
    country: "DEU",
    countryName: "Germany",
    unit: "% year on year",
    frequency: "monthly",
    seasonalAdjustment: "not seasonally adjusted",
    sourceUrl: "https://data-explorer.oecd.org/",
    observations: walk({ seed: 23, periods: months(2021, 1, 2026, 8), start: 2.1, drift: 0.01, vol: 0.35, revisedShare: 0.18, archivedFrom: "2022-06-30" }),
    nextRelease: "2026-10-14",
    state: "verified",
    reason: null,
  },
  {
    id: "oecd:DEU:unemployment",
    title: "Unemployment rate",
    provider: "OECD",
    country: "DEU",
    countryName: "Germany",
    unit: "% of labour force",
    frequency: "monthly",
    seasonalAdjustment: "seasonally adjusted",
    sourceUrl: "https://data-explorer.oecd.org/",
    observations: walk({ seed: 37, periods: months(2021, 1, 2026, 7), start: 3.6, drift: 0.008, vol: 0.06, revisedShare: 0.1, archivedFrom: "2022-06-30" }),
    nextRelease: "2026-10-09",
    state: "verified",
    reason: null,
  },
  {
    id: "bis:DEU:credit_gap",
    title: "Credit-to-GDP gap",
    provider: "BIS",
    country: "DEU",
    countryName: "Germany",
    unit: "percentage points of GDP",
    frequency: "quarterly",
    seasonalAdjustment: "not applicable",
    sourceUrl: "https://data.bis.org/topics/CREDIT_GAP",
    observations: walk({ seed: 41, periods: quarters(2014, 2026, 1), start: -2.5, vol: 1.4, revisedShare: 0.35, archivedFrom: "2020-03-31" }),
    nextRelease: "2026-12-03",
    state: "verified",
    reason: null,
  },
  {
    id: "ecb:EA:mfi_lending_rate",
    title: "Euro-area MFI lending rate, non-financial corporations",
    provider: "ECB",
    country: "EMU",
    countryName: "Euro area",
    unit: "% per annum",
    frequency: "monthly",
    seasonalAdjustment: "not applicable",
    sourceUrl: "https://data.ecb.europa.eu/",
    observations: walk({ seed: 53, periods: months(2021, 1, 2026, 8), start: 1.6, drift: 0.02, vol: 0.12, revisedShare: 0.05, archivedFrom: "2022-01-31" }),
    nextRelease: "2026-10-02",
    state: "verified",
    reason: null,
  },
];

export const COUNTRY_COMPARISON_FIXTURE: CountryComparison = {
  title: "Real GDP growth, ranked by country",
  unit: "% year on year",
  fromLabel: "2024",
  toLabel: "2025",
  higherIsRanked: "first",
  rows: [
    { country: "USA", countryName: "United States", from: 2.8, to: 2.1 },
    { country: "DEU", countryName: "Germany", from: -0.3, to: 0.4 },
    { country: "CZE", countryName: "Czechia", from: 1.1, to: 1.9 },
    { country: "CHN", countryName: "China", from: 5.0, to: 4.6 },
    { country: "JPN", countryName: "Japan", from: 0.1, to: 1.1 },
    { country: "GBR", countryName: "United Kingdom", from: 1.1, to: 1.3 },
    { country: "BRA", countryName: "Brazil", from: 3.4, to: 2.2 },
    { country: "KOR", countryName: "South Korea", from: 2.0, to: 1.0 },
    { country: "IND", countryName: "India", from: 6.5, to: 6.7 },
    { country: "TWN", countryName: "Taiwan", from: 4.8, to: null },
  ],
  provenance: {
    source: "World Bank · NY.GDP.MKTP.KD.ZG",
    sourceUrl: "https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG",
    asOf: "2026-07-01",
    state: "partial",
    reason: "Taiwan is not a World Bank reporting economy: its 2025 value is not covered, not zero.",
  },
};

const flows: TradeFlow[] = [];
const tradeRand = seeded(97);
const PARTNER_BASE: [string, number][] = [
  ["China", 5.2],
  ["Taiwan", 3.4],
  ["United States", 2.6],
  ["South Korea", 1.9],
  ["Netherlands", 1.2],
];
for (let year = 2018; year <= 2025; year++) {
  for (const [partner, base] of PARTNER_BASE) {
    // China's share shrinks and Taiwan's grows over the window: a visible concentration shift.
    const tilt = partner === "China" ? -(year - 2018) * 0.18 : partner === "Taiwan" ? (year - 2018) * 0.22 : 0;
    flows.push({ year, reporter: "Germany", partner, valueUsd: Math.round((base + tilt + (tradeRand() - 0.5) * 0.4) * 1e9) });
  }
}

export const TRADE_SLICE_FIXTURE: TradeSlice = {
  reporterName: "Germany",
  flow: "exports",
  product: "HS 8486",
  productName: "Machines for manufacturing semiconductor devices",
  hsRevision: "HS 2022",
  flows,
  unmappedUsd: { 2018: 0.9e9, 2019: 1.0e9, 2020: 1.1e9, 2021: 1.2e9, 2022: 1.3e9, 2023: 1.6e9, 2024: 1.8e9, 2025: 2.1e9 },
  valueUnit: "USD",
  sourceUrl: "https://comtradeplus.un.org/",
};

export const EXPOSURE_MAP_FIXTURE: HoldingExposureMap = {
  ticker: "FIXTURE_EQ",
  name: "Sample Equipment Co (illustrative)",
  period: "FY2025",
  weights: [
    { segment: "Systems", target: "CHN", targetName: "China", weight: 0.29, kind: "reported", evidence: "Annual report, geographic revenue note", evidenceUrl: null },
    { segment: "Systems", target: "TWN", targetName: "Taiwan", weight: 0.24, kind: "reported", evidence: "Annual report, geographic revenue note", evidenceUrl: null },
    { segment: "Systems", target: "KOR", targetName: "South Korea", weight: 0.17, kind: "reported", evidence: "Annual report, geographic revenue note", evidenceUrl: null },
    { segment: "Services", target: "USA", targetName: "United States", weight: 0.1, kind: "estimated", evidence: "Allocated by installed-base share; method v2", evidenceUrl: null },
    { segment: "Services", target: "JPN", targetName: "Japan", weight: 0.05, kind: "user_assigned", evidence: "Owner assumption, 2026-06", evidenceUrl: null },
  ],
};
