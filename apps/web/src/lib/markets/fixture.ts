import { gaussian, seeded } from "@/lib/fixtures/prng";

import { clusterOrder, correlationMatrix } from "./logic";
import type {
  Bar,
  CorporateAction,
  CorrelationReport,
  CorrelationWindow,
  ListingSeries,
  PriceDiagnostics,
  VenueCalendar,
  VenueDay,
} from "./types";

/*
 * Illustrative fixtures for the Phase 3 views. Prices are synthetic random walks for
 * invented issuers ("Sample …"); actions, calendars and disagreements are planted to show
 * every state (a split that makes raw and adjusted differ, a GBX quote, a dividend with no
 * recorded amount, an exchange closure with no national holiday, a missing bar on an open day).
 */

const r2 = (v: number) => Math.round(v * 100) / 100;

function weekdays(from: string, to: string): string[] {
  const out: string[] = [];
  for (let t = Date.parse(`${from}T00:00:00Z`); t <= Date.parse(`${to}T00:00:00Z`); t += 86_400_000) {
    const d = new Date(t);
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function makeBars(seed: number, dates: readonly string[], start: number, vol: number, drift = 0.0003): Bar[] {
  const rand = seeded(seed);
  let close = start;
  return dates.map((date) => {
    const open = close * (1 + gaussian(rand) * vol * 0.3);
    close = open * (1 + drift + gaussian(rand) * vol);
    const high = Math.max(open, close) * (1 + rand() * vol * 0.6);
    const low = Math.min(open, close) * (1 - rand() * vol * 0.6);
    return { date, open: r2(open), high: r2(high), low: r2(low), close: r2(close), volume: Math.round(800_000 + rand() * 1_400_000) };
  });
}

const START = "2025-10-01";
const END = "2026-10-01";

// ------------------------------------------------------------------------------ calendars

const day = (date: string, kind: VenueDay["kind"] = "open", reason: string | null = null, nationalHoliday: string | null = null, confirmed = true): VenueDay => ({
  date,
  kind,
  reason,
  nationalHoliday,
  confirmed,
});

function calendar(mic: string, venue: string, overrides: Record<string, Partial<VenueDay>>): VenueCalendar {
  return {
    mic,
    venue,
    days: weekdays("2026-06-01", END).map((date) => ({ ...day(date), ...overrides[date] })),
  };
}

export const VENUE_CALENDARS: Record<string, VenueCalendar> = {
  XLON: calendar("XLON", "London Stock Exchange", {
    // Scottish bank holiday: a national (regional) holiday on which London trades normally.
    "2026-08-03": { nationalHoliday: "Summer bank holiday (Scotland)" },
    "2026-08-31": { kind: "closed", reason: "Summer bank holiday", nationalHoliday: "Summer bank holiday (England and Wales)" },
  }),
  XNYS: calendar("XNYS", "New York Stock Exchange", {
    "2026-06-19": { kind: "closed", reason: "Juneteenth", nationalHoliday: "Juneteenth" },
    "2026-07-03": { kind: "closed", reason: "Independence Day (observed)", nationalHoliday: "Independence Day (observed)" },
    "2026-09-07": { kind: "closed", reason: "Labor Day", nationalHoliday: "Labor Day" },
  }),
  XPRA: calendar("XPRA", "Prague Stock Exchange", {
    "2026-07-06": { kind: "closed", reason: "Jan Hus Day", nationalHoliday: "Jan Hus Day" },
    // Ad hoc closure: a trading halt with no national holiday on the date.
    "2026-07-15": { kind: "closed", reason: "Exchange closed: systems outage (exchange notice)", nationalHoliday: null },
    "2026-09-28": { kind: "closed", reason: "St Wenceslas Day", nationalHoliday: "St Wenceslas Day" },
  }),
};

// ------------------------------------------------------------------------------ listings

const closedDates = (mic: string) => new Set(VENUE_CALENDARS[mic].days.filter((d) => d.kind === "closed").map((d) => d.date));
const tradingDates = (mic: string) => weekdays(START, END).filter((d) => !closedDates(mic).has(d));

const act = (a: Partial<CorporateAction> & Pick<CorporateAction, "id" | "date" | "type" | "source">): CorporateAction => ({
  detail: null,
  factor: null,
  sourceUrl: null,
  confirmed: true,
  ...a,
});

// London ordinary share, quoted in pence.
const ukBars = makeBars(7, tradingDates("XLON"), 1180, 0.012);

// ADR with a 4-for-1 split: adjusted series is continuous, raw jumps down on the split date.
const SPLIT_DATE = "2026-03-12";
const adrAdjusted = makeBars(19, tradingDates("XNYS").filter((d) => d !== "2026-08-12"), 41, 0.014);
const adrRaw = adrAdjusted.map((b) =>
  b.date < SPLIT_DATE ? { ...b, open: r2(b.open * 4), high: r2(b.high * 4), low: r2(b.low * 4), close: r2(b.close * 4) } : b,
);

// Prague ordinary share.
const czBars = makeBars(31, tradingDates("XPRA"), 640, 0.009);

function index100(bars: readonly Bar[], dividendBoost = 0.0001): { date: string; value: number }[] {
  let v = 100;
  return bars.map((b, i) => {
    if (i > 0) v *= (b.close / bars[i - 1].close) * (1 + dividendBoost);
    return { date: b.date, value: r2(v) };
  });
}

export const LISTINGS_FIXTURE: readonly ListingSeries[] = [
  {
    listingId: "L-SMPL-XLON",
    name: "Sample Systems plc (illustrative)",
    ticker: "SMPL",
    mic: "XLON",
    venue: "London Stock Exchange",
    kind: "ordinary",
    quoteCurrency: "GBX",
    ratioNote: null,
    source: "Yahoo Finance (source of record) · EODHD checked",
    sourceUrl: null,
    raw: ukBars,
    adjusted: ukBars,
    adjustmentReconciled: true,
    actions: [
      act({ id: "a1", date: "2025-11-06", type: "cash_dividend", detail: "9.30 GBX per share", source: "Issuer final results announcement" }),
      act({ id: "a2", date: "2026-05-14", type: "cash_dividend", detail: null, source: "Broker ledger entry without an amount", confirmed: false }),
    ],
    totalReturn: null,
    totalReturnReason: "The 14 May 2026 dividend has no recorded amount, so reinvested total return cannot be computed. Price return is shown.",
    state: "partial",
    reason: "Total return unavailable: one dividend amount missing.",
  },
  {
    listingId: "L-SDEV-XNYS",
    name: "Sample Devices ADR (illustrative)",
    ticker: "SDEV",
    mic: "XNYS",
    venue: "New York Stock Exchange",
    kind: "adr",
    quoteCurrency: "USD",
    ratioNote: "1 ADR = 0.5 ordinary share of the home-market line; the ordinary share is a separate listing under the same issuer.",
    source: "Stooq (source of record) · Twelve Data checked",
    sourceUrl: null,
    raw: adrRaw,
    adjusted: adrAdjusted,
    adjustmentReconciled: true,
    actions: [
      act({ id: "b1", date: SPLIT_DATE, type: "split", detail: "4-for-1", factor: 4, source: "Issuer press release, 2026-02-26" }),
      act({ id: "b2", date: "2026-08-20", type: "cash_dividend", detail: "0.31 USD per ADS", source: "Issuer dividend notice" }),
    ],
    totalReturn: index100(adrAdjusted),
    totalReturnReason: null,
    state: "verified",
    reason: null,
  },
  {
    listingId: "L-SFDS-XPRA",
    name: "Sample Foods a.s. (illustrative)",
    ticker: "SFDS",
    mic: "XPRA",
    venue: "Prague Stock Exchange",
    kind: "ordinary",
    quoteCurrency: "CZK",
    ratioNote: null,
    source: "Stooq (source of record)",
    sourceUrl: null,
    raw: czBars,
    adjusted: czBars,
    // A split exists but only as a suggestion: adjustment is unreconciled, so adjusted/event study are gated.
    adjustmentReconciled: false,
    actions: [act({ id: "c1", date: "2026-04-22", type: "split", detail: "2-for-1 (suggested)", factor: null, source: "Suggested from a price discontinuity; no issuer record", confirmed: false })],
    totalReturn: null,
    totalReturnReason: "The split on 22 April 2026 is unconfirmed, so the adjusted and total-return series are withheld.",
    state: "disputed",
    reason: "A suggested split has no issuer record; the series is not adjusted.",
  },
];

export const DIAGNOSTICS_FIXTURE: Record<string, PriceDiagnostics> = {
  "L-SMPL-XLON": {
    listingId: "L-SMPL-XLON",
    sourceOfRecord: "Yahoo Finance",
    alternatives: ["EODHD (free, 1 year)", "Alpha Vantage"],
    mic: "XLON",
    exchange: "London Stock Exchange",
    quoteCurrency: "GBX",
    adjustmentBasis: "Split-adjusted close; dividends not reinvested",
    lastSession: "2026-10-01",
    disagreements: [{ provider: "Alpha Vantage", date: "2026-09-24", primary: 1204.5, other: 12.05, currency: "GBX vs GBP" }],
  },
  "L-SDEV-XNYS": {
    listingId: "L-SDEV-XNYS",
    sourceOfRecord: "Stooq",
    alternatives: ["Twelve Data (free)"],
    mic: "XNYS",
    exchange: "New York Stock Exchange",
    quoteCurrency: "USD",
    adjustmentBasis: "Split-adjusted close (4-for-1, 12 Mar 2026); dividends not reinvested",
    lastSession: "2026-10-01",
    disagreements: [{ provider: "Twelve Data", date: "2026-09-18", primary: 43.12, other: 43.55, currency: "USD" }],
  },
  "L-SFDS-XPRA": {
    listingId: "L-SFDS-XPRA",
    sourceOfRecord: "Stooq",
    alternatives: [],
    mic: "XPRA",
    exchange: "Prague Stock Exchange",
    quoteCurrency: "CZK",
    adjustmentBasis: "Raw close; no confirmed adjustment",
    lastSession: "2026-10-01",
    disagreements: [],
  },
};

// ------------------------------------------------------------------------------ correlation

const CORR_KEYS = ["SMPL", "SDEV", "SFDS", "NRTH", "BLUE", "PRSM", "KITE", "ORCH"] as const;
const CORR_LABELS: Record<string, string> = {
  SMPL: "Sample Systems",
  SDEV: "Sample Devices ADR",
  SFDS: "Sample Foods",
  NRTH: "North Utilities",
  BLUE: "Blue Semis",
  PRSM: "Prism Software",
  KITE: "Kite Energy",
  ORCH: "Orchard Retail",
};

function correlationFixture(): CorrelationReport {
  const rand = seeded(1234);
  const n = 252;
  const market = Array.from({ length: n }, () => gaussian(rand) * 0.009);
  const tech = Array.from({ length: n }, () => gaussian(rand) * 0.008);
  const defensive = Array.from({ length: n }, () => gaussian(rand) * 0.006);
  const loadings: Record<string, [number, number, number]> = {
    SMPL: [0.6, 0.2, 0.2],
    SDEV: [0.7, 0.9, -0.1],
    SFDS: [0.3, -0.1, 0.9],
    NRTH: [0.2, 0, 1],
    BLUE: [0.8, 1.1, -0.2],
    PRSM: [0.7, 1.0, -0.1],
    KITE: [0.9, 0.1, 0.1],
    ORCH: [0.5, 0, 0.6],
  };
  // ORCH listed recently: only 15 observations, so it cannot be correlated with confidence.
  const history: Record<string, number> = { ORCH: 15, KITE: 140 };
  const series = CORR_KEYS.map((k) => {
    const [a, b, c] = loadings[k];
    const have = history[k] ?? n;
    return Array.from({ length: n }, (_, i): number | null =>
      i < n - have ? null : a * market[i] + b * tech[i] + c * defensive[i] + gaussian(rand) * 0.006,
    );
  });
  const windows: CorrelationWindow[] = [
    { id: "3M", label: "3 months", size: 63 },
    { id: "6M", label: "6 months", size: 126 },
    { id: "1Y", label: "1 year", size: 252 },
  ].map(({ id, label, size }) => {
    const minShared = id === "3M" ? 30 : 60;
    const slice = series.map((s) => s.slice(n - size));
    const { matrix, counts } = correlationMatrix(slice, minShared);
    const order = clusterOrder(matrix);
    return {
      id,
      label,
      order: order.map((i) => CORR_KEYS[i]),
      matrix: order.map((i) => order.map((j) => matrix[i][j])),
      counts: order.map((i) => order.map((j) => counts[i][j])),
      minShared,
    };
  });
  return {
    labels: CORR_LABELS,
    windows,
    source: "Daily simple returns from the primary price source of record, adjusted close",
    asOf: END,
    state: "partial",
    reason: "Pairs with too few shared sessions are left blank, not set to zero.",
  };
}

export const CORRELATION_FIXTURE: CorrelationReport = correlationFixture();
