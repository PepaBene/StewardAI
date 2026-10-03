import { describe, expect, it } from "vitest";

import {
  addDays,
  assembleCorrelation,
  buildSessionCells,
  chosenSeries,
  clusterOrder,
  describeAction,
  describeQuote,
  eventStudyGate,
  eventWindows,
  meanWindow,
  placeActions,
  portfolioPriceCoverage,
  seriesCompleteness,
  seriesState,
  sessionCompleteness,
  sessionCounts,
  unexplainedJumps,
  weekdaysBetween,
} from "./logic";
import type { CorporateAction, HoldingPrices, PriceDiagnostic, ReturnsReport } from "./types";

function action(over: Partial<CorporateAction> = {}): CorporateAction {
  return {
    id: 1,
    symbol: "ABC",
    type: "split",
    exDate: "2026-09-10",
    recordDate: null,
    paymentDate: null,
    ratio: 4,
    cashAmount: null,
    currency: null,
    source: "Issuer",
    sourceUrl: null,
    knownAt: null,
    evidenceId: 1,
    status: "confirmed",
    decidedAt: null,
    decisionReason: null,
    per: null,
    amountBasis: null,
    ...over,
  };
}

function diag(over: Partial<PriceDiagnostic> = {}): PriceDiagnostic {
  return {
    symbol: "abc.us",
    source: "stooq",
    mic: "XNAS",
    currency: "USD",
    quote_unit: "USD",
    bar_type: "adjusted",
    last_session: "2026-10-02",
    observed_sessions: 100,
    disagreements: 0,
    missing_days: 0,
    access_state: "available",
    chosen: true,
    expected_sessions: 100,
    calendar_basis: "confirmed_calendar",
    stale_sessions: 0,
    ...over,
  };
}

function returns(over: Partial<NonNullable<ReturnsReport["result"]>> = {}): ReturnsReport {
  return {
    ticker: "ABC",
    knownAsOf: "2026-10-03",
    reason: null,
    result: {
      symbol: "abc.us",
      source: "stooq",
      currency: "USD",
      mic: "XNAS",
      priceReturn: { value: 0.1, reason: null },
      rawPriceReturn: { value: -0.7, reason: null },
      totalReturn: { value: 0.12, reason: null },
      actionCoverage: "available",
      coverage: 1,
      expectedSessions: 250,
      observedSessions: 250,
      calendarBasis: "confirmed_calendar",
      missingActions: { priceRaw: [], priceAdjusted: [], totalReturn: [] },
      start: "2025-10-03",
      end: "2026-10-03",
      ...over,
    },
  };
}

describe("dates", () => {
  it("adds days across month ends and lists weekdays only", () => {
    expect(addDays("2026-02-27", 3)).toBe("2026-03-02");
    expect(weekdaysBetween("2026-10-02", "2026-10-06")).toEqual(["2026-10-02", "2026-10-05", "2026-10-06"]);
    expect(weekdaysBetween("2026-10-03", "2026-10-04")).toEqual([]);
  });
});

describe("quote units", () => {
  it("shows pence and the pound value together, and leaves a major currency alone", () => {
    expect(describeQuote(1240.5, "GBX")).toBe("1,240.50 GBX (12.41 GBP)");
    expect(describeQuote(12.34, "USD")).toBe("12.34 USD");
  });
  it("describes a dividend amount with its unit and ADS basis, and a missing amount as null", () => {
    expect(describeAction(action({ type: "cash_dividend", ratio: null, cashAmount: 31, currency: "GBp", per: "ordinary_share" }))).toBe("31 GBp per ordinary share");
    expect(describeAction(action({ type: "cash_dividend", ratio: null, cashAmount: null }))).toBeNull();
    expect(describeAction(action())).toBe("ratio 4");
  });
});

describe("series selection and completeness", () => {
  it("serves the chosen adjusted row and never invents a share without a denominator", () => {
    const rows = [diag({ chosen: false, bar_type: "raw" }), diag()];
    expect(chosenSeries(rows)?.bar_type).toBe("adjusted");
    expect(seriesCompleteness({ expected_sessions: 0, missing_days: 0 })).toBeNull();
    expect(seriesCompleteness({ expected_sessions: 100, missing_days: 5 })).toBeCloseTo(0.95);
  });
});

describe("seriesState", () => {
  const base = { barCount: 10, gap: null, plausibility: null, chosen: diag() };
  it("is not covered with no bars, carrying the backend's gap reason", () => {
    expect(seriesState({ ...base, barCount: 0, gap: "no Stooq symbol" })).toEqual({ state: "not_covered", reason: "no Stooq symbol" });
  });
  it("prefers disputed (plausibility, disagreements) over stale over partial", () => {
    expect(seriesState({ ...base, plausibility: { flagged: true, difference_pct: 4.2, date: "2026-10-01" } }).state).toBe("disputed");
    expect(seriesState({ ...base, chosen: diag({ disagreements: 2, stale_sessions: 3, missing_days: 4 }) }).state).toBe("disputed");
    expect(seriesState({ ...base, chosen: diag({ stale_sessions: 3, missing_days: 4 }) }).state).toBe("stale");
    expect(seriesState({ ...base, chosen: diag({ missing_days: 4 }) }).state).toBe("partial");
    expect(seriesState(base).state).toBe("verified");
  });
  it("is partial, never verified, without diagnostics, and honours a non-available access state", () => {
    expect(seriesState({ ...base, chosen: null }).state).toBe("partial");
    expect(seriesState({ ...base, chosen: diag({ access_state: "paid_only" }) }).state).toBe("paid_only");
    expect(seriesState({ ...base, chosen: diag({ access_state: "weird" }) }).state).toBe("unavailable");
  });
});

describe("action markers", () => {
  const bars = [{ date: "2026-09-08" }, { date: "2026-09-09" }, { date: "2026-09-14" }];
  it("places an action at the first session on or after its ex-date and flags a shift", () => {
    const placed = placeActions(bars, [action({ exDate: "2026-09-09" }), action({ id: 2, exDate: "2026-09-12" }), action({ id: 3, exDate: "2026-08-01" })]);
    expect(placed.map((p) => [p.index, p.shifted])).toEqual([
      [1, false],
      [2, true],
      [null, false],
    ]);
  });
  it("flags a big move only when no non-rejected action explains it", () => {
    const series = [
      { date: "2026-09-08", close: 100 },
      { date: "2026-09-09", close: 25 },
      { date: "2026-09-10", close: 60 },
    ];
    expect(unexplainedJumps(series, [action({ exDate: "2026-09-09" })]).map((j) => j.date)).toEqual(["2026-09-10"]);
    expect(unexplainedJumps(series, [action({ exDate: "2026-09-09", status: "rejected" })]).map((j) => j.date)).toEqual(["2026-09-09", "2026-09-10"]);
    expect(unexplainedJumps([{ date: "a", close: null }, { date: "b", close: 5 }], [])).toEqual([]);
  });
});

describe("event study", () => {
  const closes = Array.from({ length: 30 }, (_, i) => ({ date: addDays("2026-09-01", i), close: 100 + i }));
  it("indexes to 100 at the session before the event and omits windows that run off the series", () => {
    const w = eventWindows(closes, [{ date: "2026-09-15", label: "mid" }, { date: "2026-09-02", label: "early" }, { date: "2026-09-29", label: "late" }], 3, 3);
    expect(w.map((x) => x.label)).toEqual(["mid"]);
    expect(w[0].points.find((p) => p.offset === -1)!.value).toBeCloseTo(100 * (113 / 113));
    expect(w[0].points.find((p) => p.offset === 0)!.value).toBeCloseTo((114 / 113) * 100);
    expect(meanWindow(w)!.length).toBe(7);
    expect(meanWindow([])).toBeNull();
  });
  it("skips a window containing a missing close instead of padding it", () => {
    const gappy = closes.map((b, i) => (i === 14 ? { ...b, close: null } : b));
    expect(eventWindows(gappy, [{ date: "2026-09-15", label: "x" }], 3, 3)).toEqual([]);
  });
  it("is allowed only when coverage is verified, nothing is flagged and no split is open", () => {
    const ok = { hasAdjustedBars: true, returns: returns(), actions: [action()] };
    expect(eventStudyGate(ok)).toEqual({ allowed: true });
    expect(eventStudyGate({ ...ok, hasAdjustedBars: false }).allowed).toBe(false);
    expect(eventStudyGate({ ...ok, returns: null }).allowed).toBe(false);
    expect(eventStudyGate({ ...ok, returns: { ticker: "ABC", knownAsOf: null, result: null, reason: "no stored price series" } })).toEqual({ allowed: false, reason: "no stored price series" });
    expect(eventStudyGate({ ...ok, returns: returns({ actionCoverage: "incomplete" }) }).allowed).toBe(false);
    expect(eventStudyGate({ ...ok, returns: returns({ missingActions: { priceRaw: [], priceAdjusted: ["split on 2026-09-10 is not valued"], totalReturn: [] } }) }).allowed).toBe(false);
    const gate = eventStudyGate({ ...ok, actions: [action({ status: "suggested" })] });
    expect(gate.allowed).toBe(false);
    expect(eventStudyGate({ ...ok, actions: [action({ status: "rejected" })] }).allowed).toBe(true);
  });
});

describe("correlation arrangement", () => {
  const rows = [
    { ticker: "A", correlations: [{ ticker: "B", correlation: 0.8 }, { ticker: "C", correlation: null }] },
    { ticker: "B", correlations: [{ ticker: "A", correlation: 0.8 }, { ticker: "C", correlation: -0.2 }] },
    { ticker: "C", correlations: [{ ticker: "A", correlation: null }, { ticker: "B", correlation: -0.2 }] },
    { ticker: "D", correlations: null },
  ];
  it("keeps backend values, mirrors a one-sided pair, leaves blanks null and drops series-less holdings", () => {
    const g = assembleCorrelation(rows);
    expect(g.tickers).toEqual(["A", "B", "C"]);
    expect(g.matrix).toEqual([
      [1, 0.8, null],
      [0.8, 1, -0.2],
      [null, -0.2, 1],
    ]);
    expect(g.withoutSeries).toEqual(["D"]);
    expect(g.asymmetricPairs).toBe(0);
  });
  it("mirrors when only the other holding returned the value, and counts asymmetric pairs", () => {
    const g = assembleCorrelation([
      { ticker: "A", correlations: [{ ticker: "B", correlation: null }] },
      { ticker: "B", correlations: [{ ticker: "A", correlation: 0.5 }] },
    ]);
    expect(g.matrix[0][1]).toBe(0.5);
    const asym = assembleCorrelation([
      { ticker: "A", correlations: [{ ticker: "B", correlation: 0.5 }] },
      { ticker: "B", correlations: [{ ticker: "A", correlation: 0.3 }] },
    ]);
    expect(asym.asymmetricPairs).toBe(1);
    expect(asym.matrix[0][1]).toBe(0.5);
  });
  it("orders correlated holdings together; a missing value counts as unrelated, not identical", () => {
    const m = [
      [1, 0.1, 0.9],
      [0.1, 1, 0.2],
      [0.9, 0.2, 1],
    ];
    const order = clusterOrder(m);
    expect(Math.abs(order.indexOf(0) - order.indexOf(2))).toBe(1);
    const blank = [
      [1, null, 0.9],
      [null, 1, null],
      [0.9, null, 1],
    ];
    const o = clusterOrder(blank);
    expect(Math.abs(o.indexOf(0) - o.indexOf(2))).toBe(1);
    expect(clusterOrder([])).toEqual([]);
  });
});

describe("sessions", () => {
  // Mon 2026-09-28 .. Fri 2026-10-09 with today = Wed 2026-10-07.
  const base = {
    mic: "XPRA",
    start: "2026-09-28",
    end: "2026-10-09",
    today: "2026-10-07",
    sessions: [],
    calendar: { closures: [], suggestions: [] },
    firstBar: "2026-09-01",
  };
  const days = weekdaysBetween(base.start, base.end);
  const expected = (excluded: string[] = []) => ({ mic: "XPRA", start: base.start, end: base.end, count: 0, basis: "weekdays_minus_configured_closures", confirmedDays: 0, days: days.filter((d) => !excluded.includes(d)) });
  const status = (cells: ReturnType<typeof buildSessionCells>, d: string) => cells.find((c) => c.date === d)!.status;

  it("separates a missing bar, a holiday, an ad hoc closure, a national holiday with the exchange open, and future days", () => {
    const cells = buildSessionCells({
      ...base,
      expected: expected(["2026-09-28", "2026-09-29"]),
      calendar: { closures: [{ mic: "XPRA", date: "2026-09-28", name: "Statehood Day" }], suggestions: [{ mic: "XPRA", date: "2026-10-01", name: "Local day" }, { mic: "XPRA", date: "2026-10-02", name: "Another" }] },
      sessions: [{ mic: "XPRA", sessionDate: "2026-09-29", state: "closed", source: "exchange", confirmed: true, reason: "Technical halt", evidenceUrl: null, knownAt: null }],
      // Held: 09-30, 10-01 (national holiday, open), not 10-02 (national holiday, open, but no bar), 10-05, 10-06.
      barDates: ["2026-09-30", "2026-10-01", "2026-10-05", "2026-10-06", "2026-10-07"],
    });
    expect(status(cells, "2026-09-28")).toBe("exchange_holiday");
    expect(status(cells, "2026-09-29")).toBe("exchange_closure");
    expect(status(cells, "2026-09-30")).toBe("trading");
    expect(status(cells, "2026-10-01")).toBe("national_holiday_open");
    expect(status(cells, "2026-10-02")).toBe("missing_bar");
    expect(cells.find((c) => c.date === "2026-10-02")!.note).toMatch(/still a gap/);
    expect(status(cells, "2026-10-08")).toBe("upcoming");
    const counts = sessionCounts(cells);
    expect(counts.missing_bar).toBe(1);
    expect(sessionCompleteness(cells)).toEqual({ held: 5, expected: 6, share: 5 / 6 });
  });
  it("does not count today without a bar, or days before the first stored bar, as missing", () => {
    const cells = buildSessionCells({ ...base, expected: expected(), firstBar: "2026-10-01", barDates: ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"] });
    expect(status(cells, "2026-09-29")).toBe("before_history");
    expect(status(cells, "2026-10-07")).toBe("upcoming");
  });
  it("marks a confirmed half day, treats a holiday-worded closed row as a holiday, and has no share without expected sessions", () => {
    const cells = buildSessionCells({
      ...base,
      expected: expected(["2026-09-30"]),
      sessions: [
        { mic: "XPRA", sessionDate: "2026-10-01", state: "half_day", source: "exchange", confirmed: true, reason: "Early close", evidenceUrl: null, knownAt: null },
        { mic: "XPRA", sessionDate: "2026-09-30", state: "closed", source: "exchange", confirmed: true, reason: "Public holiday", evidenceUrl: null, knownAt: null },
      ],
      barDates: ["2026-09-28", "2026-09-29", "2026-10-01"],
    });
    expect(status(cells, "2026-10-01")).toBe("half_day");
    expect(status(cells, "2026-09-30")).toBe("exchange_holiday");
    expect(sessionCompleteness([])).toEqual({ held: 0, expected: 0, share: null });
  });
});

describe("portfolio price coverage", () => {
  const h = (ticker: string, as_of: string | null, bars: number): HoldingPrices => ({
    ticker,
    name: null,
    symbol: null,
    source: null,
    as_of,
    last_close: null,
    previous_close: null,
    day_change_pct: null,
    bars: Array.from({ length: bars }, (_, i) => ({ date: addDays("2026-09-01", i), close: 1 })),
  });
  it("names holdings without bars and holdings behind the newest, and never counts missing as flat", () => {
    const c = portfolioPriceCoverage([h("A", "2026-10-02", 20), h("B", null, 0), h("C", "2026-09-20", 10)]);
    expect(c).toMatchObject({ total: 3, withBars: 2, withoutBars: ["B"], stale: ["C"], newestAsOf: "2026-10-02" });
    expect(portfolioPriceCoverage([])).toMatchObject({ total: 0, withBars: 0, newestAsOf: null });
  });
});
