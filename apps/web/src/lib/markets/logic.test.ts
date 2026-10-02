import { describe, expect, it } from "vitest";

import { CORRELATION_FIXTURE, DIAGNOSTICS_FIXTURE, LISTINGS_FIXTURE, VENUE_CALENDARS } from "./fixture";
import {
  barsForBasis,
  classifySessions,
  clusterOrder,
  completeness,
  correlationMatrix,
  describeQuote,
  eventStudyGate,
  eventWindows,
  looksLikeUnitError,
  majorUnit,
  meanWindow,
  pairCorrelation,
  placeActions,
  sessionCounts,
  sharedObservationStats,
  simpleReturns,
  totalReturnBlockers,
  unexplainedJumps,
} from "./logic";
import type { Bar, CorporateAction } from "./types";

const [uk, adr, cz] = LISTINGS_FIXTURE;
const bar = (date: string, close: number): Bar => ({ date, open: close, high: close, low: close, close, volume: null });
const action = (date: string, extra: Partial<CorporateAction> = {}): CorporateAction => ({
  id: date,
  date,
  type: "cash_dividend",
  detail: "1 USD",
  factor: null,
  source: "issuer",
  sourceUrl: null,
  confirmed: true,
  ...extra,
});

describe("quote units", () => {
  it("never lets a pence quote pass for pounds", () => {
    expect(majorUnit("GBX")).toEqual({ currency: "GBP", divisor: 100 });
    expect(majorUnit("USD")).toEqual({ currency: "USD", divisor: 1 });
    expect(describeQuote(1240.5, "GBX")).toBe("1,240.50 GBX (12.41 GBP)");
    expect(describeQuote(43.1, "USD")).toBe("43.10 USD");
  });

  it("recognises a 100x disagreement as a unit error, not a price difference", () => {
    expect(looksLikeUnitError({ primary: 1204.5, other: 12.045 })).toBe(true);
    expect(looksLikeUnitError({ primary: 43.12, other: 43.55 })).toBe(false);
    expect(looksLikeUnitError({ primary: 10, other: 50 })).toBe(false);
  });
});

describe("action placement", () => {
  const bars = ["2026-03-10", "2026-03-11", "2026-03-13"].map((d) => bar(d, 10));
  it("places on the first session on or after the date and flags a shift", () => {
    const [p] = placeActions(bars, [action("2026-03-12")]);
    expect(p).toMatchObject({ index: 2, shifted: true });
    expect(placeActions(bars, [action("2026-03-11")])[0]).toMatchObject({ index: 1, shifted: false });
  });
  it("leaves out-of-range actions unplaced", () => {
    expect(placeActions(bars, [action("2025-01-01"), action("2027-01-01")]).map((p) => p.index)).toEqual([null, null]);
  });
  it("the ADR fixture's split lands on its own session", () => {
    const placed = placeActions(adr.raw, adr.actions).find((p) => p.action.type === "split")!;
    expect(adr.raw[placed.index!].date).toBe("2026-03-12");
    expect(placed.shifted).toBe(false);
  });
});

describe("raw versus adjusted series", () => {
  it("shows a 4x step in the raw ADR series that the adjusted series does not have", () => {
    const i = adr.raw.findIndex((b) => b.date === "2026-03-12");
    const rawJump = adr.raw[i].close / adr.raw[i - 1].close;
    const adjJump = adr.adjusted![i].close / adr.adjusted![i - 1].close;
    expect(rawJump).toBeLessThan(0.4);
    expect(adjJump).toBeGreaterThan(0.85);
  });

  it("the economic value across the split is preserved: raw close x factor equals adjusted before the split", () => {
    const split = adr.actions.find((a) => a.type === "split")!;
    const i = adr.raw.findIndex((b) => b.date === split.date);
    expect(adr.raw[i - 1].close / split.factor!).toBeCloseTo(adr.adjusted![i - 1].close, 1);
  });

  it("flags a large move no action explains, but not one a recorded action does", () => {
    expect(unexplainedJumps(adr.raw, adr.actions)).toEqual([]);
    expect(unexplainedJumps(adr.raw, []).map((j) => j.date)).toEqual(["2026-03-12"]);
  });

  it("refuses an unreconciled adjusted series instead of substituting raw under its name", () => {
    expect(barsForBasis(cz, "adjusted")).toMatchObject({ available: false });
    expect(barsForBasis(cz, "raw").available).toBe(true);
    expect(barsForBasis(adr, "adjusted").available).toBe(true);
  });

  it("withholds total return while a dividend has no amount or a split is unconfirmed", () => {
    expect(uk.totalReturn).toBeNull();
    expect(totalReturnBlockers(uk.actions)).toHaveLength(1);
    expect(totalReturnBlockers(cz.actions)).toHaveLength(1);
    expect(totalReturnBlockers(adr.actions)).toEqual([]);
    expect(adr.totalReturn).not.toBeNull();
  });
});

describe("event study", () => {
  const bars = Array.from({ length: 20 }, (_, i) => bar(`2026-01-${String(i + 1).padStart(2, "0")}`, 100 + i));
  it("indexes to 100 at the last session before the event and omits windows that run off the series", () => {
    const w = eventWindows(bars, [{ date: "2026-01-10", label: "e" }, { date: "2026-01-02", label: "early" }, { date: "2026-01-19", label: "late" }], 3, 3);
    expect(w).toHaveLength(1);
    const at = (o: number) => w[0].points.find((p) => p.offset === o)!.value;
    expect(at(0)).toBeCloseTo((109 / 108) * 100);
    expect(at(-1)).toBeCloseTo((108 / 108) * 100);
  });
  it("averages identical-shape windows and returns null for none", () => {
    const w = eventWindows(bars, [{ date: "2026-01-08", label: "a" }, { date: "2026-01-12", label: "b" }], 2, 2);
    expect(meanWindow(w)).toHaveLength(5);
    expect(meanWindow([])).toBeNull();
  });
  it("is gated until the adjustment is reconciled and every split confirmed", () => {
    expect(eventStudyGate(adr)).toEqual({ allowed: true });
    expect(eventStudyGate(cz)).toMatchObject({ allowed: false });
  });
});

describe("correlation", () => {
  it("computes Pearson r on shared observations and returns null below the minimum", () => {
    const a = Array.from({ length: 30 }, (_, i) => i + (i % 3));
    const b = a.map((v) => v * 2 + 1);
    expect(pairCorrelation(a, b, 20).r).toBeCloseTo(1);
    expect(pairCorrelation(a, a.map((v) => -v), 20).r).toBeCloseTo(-1);
    expect(pairCorrelation(a, b.map((v, i) => (i < 5 ? v : null)), 20)).toEqual({ r: null, shared: 5 });
    expect(pairCorrelation([1, 1, 1, 1], [1, 2, 3, 4], 3).r).toBeNull();
  });

  it("builds a symmetric matrix with a unit diagonal and counts", () => {
    const s = [Array.from({ length: 30 }, (_, i) => Math.sin(i)), Array.from({ length: 30 }, (_, i) => Math.sin(i) * 2), Array.from({ length: 30 }, (_, i) => Math.cos(i * 3))];
    const { matrix, counts } = correlationMatrix(s, 10);
    expect(matrix[0][0]).toBe(1);
    expect(matrix[0][1]).toBeCloseTo(1);
    expect(matrix[1][0]).toBe(matrix[0][1]);
    expect(counts[0][2]).toBe(30);
  });

  it("orders so that correlated listings are adjacent", () => {
    // 0 and 2 are strongly correlated, 1 is independent of both.
    const m = [
      [1, 0.1, 0.9],
      [0.1, 1, 0.05],
      [0.9, 0.05, 1],
    ];
    const order = clusterOrder(m);
    expect(Math.abs(order.indexOf(0) - order.indexOf(2))).toBe(1);
    expect([...order].sort()).toEqual([0, 1, 2]);
  });

  it("treats a missing correlation as unrelated, not as perfectly similar", () => {
    const m = [
      [1, null, 0.2],
      [null, 1, 0.8],
      [0.2, 0.8, 1],
    ];
    const order = clusterOrder(m);
    expect(Math.abs(order.indexOf(1) - order.indexOf(2))).toBe(1);
  });

  it("the fixture leaves a thinly-observed pair blank and reports shared counts", () => {
    const w = CORRELATION_FIXTURE.windows.find((x) => x.id === "1Y")!;
    const iO = w.order.indexOf("ORCH");
    const offDiagonal = w.matrix[iO].filter((_, j) => j !== iO);
    expect(offDiagonal.every((v) => v === null)).toBe(true);
    expect(w.counts[iO][iO === 0 ? 1 : 0]).toBe(15);
    const stats = sharedObservationStats(w.counts)!;
    expect(stats.min).toBe(15);
    expect(stats.median).toBeGreaterThan(100);
    // Every window is symmetric.
    for (const win of CORRELATION_FIXTURE.windows) for (let i = 0; i < win.order.length; i++) for (let j = 0; j < win.order.length; j++) expect(win.matrix[i][j]).toBe(win.matrix[j][i]);
  });

  it("simple returns leave a gap where either close is missing", () => {
    expect(simpleReturns([100, 110, null, 121])).toEqual([null, 0.10000000000000009, null, null]);
  });
});

describe("sessions", () => {
  const cal = (days: [string, "open" | "half_day" | "closed", string | null][]) => ({
    mic: "X",
    venue: "X",
    days: days.map(([date, kind, nat]) => ({ date, kind, reason: kind === "closed" ? "closed" : null, nationalHoliday: nat, confirmed: true })),
  });

  it("separates a genuine missing bar from an exchange holiday, an ad hoc closure and a national holiday on an open day", () => {
    const cells = classifySessions(
      cal([
        ["2026-07-01", "open", null],
        ["2026-07-02", "open", null], // no bar held
        ["2026-07-03", "closed", "Independence Day"],
        ["2026-07-06", "closed", null], // closure without a national holiday
        ["2026-07-07", "open", "Regional holiday"], // national holiday, venue trades
        ["2026-07-08", "half_day", null],
      ]),
      ["2026-07-01", "2026-07-07", "2026-07-08"],
    );
    expect(cells.map((c) => c.status)).toEqual(["trading", "missing_bar", "exchange_holiday", "exchange_closure", "national_holiday_open", "half_day"]);
  });

  it("a national holiday does not excuse a missing bar when the exchange was open", () => {
    const [c] = classifySessions(cal([["2026-08-03", "open", "Summer bank holiday (Scotland)"]]), []);
    expect(c.status).toBe("missing_bar");
  });

  it("measures completeness over open days only", () => {
    const cells = classifySessions(cal([["2026-07-01", "open", null], ["2026-07-02", "open", null], ["2026-07-03", "closed", "H"]]), ["2026-07-01"]);
    expect(completeness(cells)).toBe(0.5);
    expect(sessionCounts(cells).exchange_holiday).toBe(1);
    expect(completeness(classifySessions(cal([["2026-07-03", "closed", "H"]]), []))).toBeNull();
  });

  it("the fixture planted one missing bar, one ad hoc closure and one national-holiday trading day", () => {
    const adrCells = classifySessions(VENUE_CALENDARS.XNYS, adr.raw.map((b) => b.date));
    expect(adrCells.filter((c) => c.status === "missing_bar").map((c) => c.date)).toEqual(["2026-08-12"]);
    const czCells = classifySessions(VENUE_CALENDARS.XPRA, cz.raw.map((b) => b.date));
    expect(czCells.find((c) => c.date === "2026-07-15")!.status).toBe("exchange_closure");
    expect(sessionCounts(classifySessions(VENUE_CALENDARS.XLON, uk.raw.map((b) => b.date))).national_holiday_open).toBe(1);
  });
});

describe("fixture diagnostics", () => {
  it("has diagnostics for every listing, with the planted GBX/GBP unit mismatch flagged", () => {
    for (const l of LISTINGS_FIXTURE) expect(DIAGNOSTICS_FIXTURE[l.listingId]).toBeDefined();
    const d = DIAGNOSTICS_FIXTURE["L-SMPL-XLON"].disagreements[0];
    expect(looksLikeUnitError(d)).toBe(true);
  });
});
