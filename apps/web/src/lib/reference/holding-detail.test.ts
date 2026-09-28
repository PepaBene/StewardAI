import { describe, expect, it } from "vitest";

import {
  annualisedVolatility,
  barsInRange,
  buildTimeline,
  exchangeName,
  fromYearHigh,
  historicalVar,
  holderBars,
  kpiBar,
  kpiHeadroom,
  lastCloseChange,
  radarValue,
  reportCountdown,
  scoreChange,
  shortPeriod,
  thesisRadar,
  yearMaxDrawdown,
} from "./holding-detail";

const day = (i: number) => new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10);

describe("price helpers", () => {
  const bars = [100, 102, 101, 105].map((close, i) => ({ date: day(i), close }));

  it("slices a range back from the latest bar", () => {
    expect(barsInRange(bars, 2).map((b) => b.close)).toEqual([101, 105]);
  });

  it("measures the last close against the previous one", () => {
    const c = lastCloseChange(bars);
    expect(c?.change).toBe(4);
    expect(c?.pct).toBeCloseTo(4 / 101);
    expect(c?.date).toBe(day(3));
    expect(lastCloseChange(bars.slice(0, 1))).toBeNull();
  });

  it("finds the distance from the trailing-year high and the worst fall", () => {
    const hi = fromYearHigh(bars);
    expect(hi?.high).toBe(105);
    expect(hi?.drawdown).toBe(0);
    const falling = [100, 120, 90, 96].map((close, i) => ({ date: day(i), close }));
    expect(fromYearHigh(falling)?.drawdown).toBeCloseTo(-0.2);
    expect(yearMaxDrawdown(falling)).toEqual({ value: -0.25, date: day(2) });
  });
});

describe("risk from closes", () => {
  it("needs twenty returns for volatility and VaR", () => {
    const short = Array.from({ length: 20 }, (_, i) => 100 + i);
    expect(annualisedVolatility(short)).toBeNull();
    expect(historicalVar(short)).toBeNull();
  });

  it("is zero volatility for a constant growth rate", () => {
    const steady = Array.from({ length: 40 }, (_, i) => 100 * 1.01 ** i);
    expect(annualisedVolatility(steady)).toBeCloseTo(0, 10);
  });

  it("annualises the sample deviation of log returns", () => {
    // Alternating ±r log returns: sample sd = r·sqrt(n/(n−1)).
    const r = 0.01;
    const closes = [100];
    for (let i = 0; i < 40; i++) closes.push(closes[i] * Math.exp(i % 2 ? -r : r));
    const n = 40;
    expect(annualisedVolatility(closes)).toBeCloseTo(r * Math.sqrt(n / (n - 1)) * Math.sqrt(252), 8);
  });

  it("takes the nearest-rank 5th percentile daily return as the 95% VaR", () => {
    // 40 returns: 38 of +1% and two losses; rank ceil(0.05·40) = 2 -> the second-worst.
    const closes = [100];
    const rets = [...Array(38).fill(0.01), -0.05, -0.03];
    for (const x of rets) closes.push(closes[closes.length - 1] * (1 + x));
    expect(historicalVar(closes)).toBeCloseTo(-0.03, 10);
  });
});

describe("KPI headroom", () => {
  it("is observed over threshold for a floor, threshold over observed for a ceiling", () => {
    expect(kpiHeadroom(">=", 4, 5.54)).toBeCloseTo(1.385);
    expect(kpiHeadroom("<=", 30, 41)).toBeCloseTo(30 / 41);
    expect(kpiHeadroom("<", 30, 0)).toBe(2);
    expect(kpiHeadroom("==", 10, 9)).toBeCloseTo(0.9);
  });

  it("cannot be measured without an observation or against a zero threshold", () => {
    expect(kpiHeadroom(">=", 4, null)).toBeNull();
    expect(kpiHeadroom(">=", 0, 3)).toBeNull();
    expect(kpiHeadroom("==", 0, 3)).toBeNull();
  });

  it("puts the threshold on the middle ring and clamps at the rim", () => {
    expect(radarValue(1)).toBe(0.5);
    expect(radarValue(3)).toBe(1);
  });

  const kpi = (id: string, operator: string, threshold: number, obs: (number | null)[]) => ({
    kpi: { id, metric: id, operator, threshold },
    current: obs[0] === undefined ? null : { period: "FY2026 Q2", observed_value: obs[0], status: "pass" },
    history: obs.map((o, i) => ({ period: i === 0 ? "FY2026 Q2" : "FY2026 Q1", observed_value: o, status: "pass" })),
  });

  it("draws a radar of three or more measurable KPIs with the previous verdict", () => {
    const r = thesisRadar(
      [kpi("a", ">=", 4, [6, 4]), kpi("b", "<=", 30, [30, 60]), kpi("c", ">=", 50, [50, 50])],
      (m) => m.toUpperCase(),
    );
    expect(r?.axes).toEqual(["A", "B", "C"]);
    expect(r?.now).toEqual([0.75, 0.5, 0.5]);
    expect(r?.previous).toEqual([0.5, 0.25, 0.5]);
    expect(r?.previousLabel).toBe("FY2026 Q1 verdict");
  });

  it("drops the comparison when a KPI has no earlier verdict, and the radar below three axes", () => {
    const r = thesisRadar([kpi("a", ">=", 4, [6]), kpi("b", "<=", 30, [30, 60]), kpi("c", ">=", 50, [50, 50])], (m) => m);
    expect(r?.previous).toBeNull();
    expect(thesisRadar([kpi("a", ">=", 4, [6]), kpi("b", ">=", 4, [null])], (m) => m)).toBeNull();
  });

  it("bars the observation against its threshold and names the side", () => {
    expect(kpiBar(">=", 4, 5)).toMatchObject({ min: 0, max: 6.25, value: 5, target: 4, side: "inside" });
    expect(kpiBar("<=", 30, 41).side).toBe("above ceiling");
    expect(kpiBar(">=", 50, 40).side).toBe("below floor");
    expect(kpiBar(">=", 4, null)).toMatchObject({ value: null, side: null });
    expect(kpiBar(">=", 2, -4).min).toBe(-5);
  });
});

describe("thesis score change", () => {
  const history = [
    { date: "2026-04-24", score: 33 },
    { date: "2026-07-24", score: 50 },
  ];
  it("compares with the last point when the score has moved since", () => {
    expect(scoreChange(67, history)).toEqual({ prior: 50, date: "2026-07-24", direction: "up" });
  });
  it("compares with the point before when the last one is the current score", () => {
    expect(scoreChange(50, history)).toEqual({ prior: 33, date: "2026-04-24", direction: "up" });
    expect(scoreChange(50, history.slice(1))).toBeNull();
    expect(scoreChange(null, history)).toBeNull();
  });
});

describe("report countdown", () => {
  it("counts days and the elapsed share of the reporting interval", () => {
    expect(reportCountdown("2026-10-15", "2026-07-17", "2026-09-27")).toEqual({ days: 18, elapsed: 72 / 90 });
    expect(reportCountdown("2026-10-15", null, "2026-09-27")).toEqual({ days: 18, elapsed: null });
    expect(reportCountdown(null, null, "2026-09-27")).toBeNull();
  });
});

describe("periods and exchanges", () => {
  it("shortens fiscal periods", () => {
    expect(shortPeriod("FY2026 Q2")).toBe("Q2·26");
    expect(shortPeriod("FY2025")).toBe("FY25");
    expect(shortPeriod("2026-06")).toBe("2026-06");
  });
  it("names common exchanges and falls back to the code", () => {
    expect(exchangeName("XAMS")).toBe("Euronext Amsterdam");
    expect(exchangeName("XABC")).toBe("XABC");
    expect(exchangeName(null)).toBeNull();
  });
});

describe("timeline", () => {
  it("folds a verdict run into one line and merges it with news, newest first", () => {
    const t = buildTimeline(
      [
        { kpiLabel: "Net bookings", period: "FY2026 Q2", status: "pass", observed: "5.54", target: "≥ 4", created_at: "2026-07-24T08:08:00Z", factCount: 2 },
        { kpiLabel: "China share", period: "FY2026 Q2", status: "breach", observed: "41%", target: "≤ 30%", created_at: "2026-07-24T08:08:00Z", factCount: 1 },
      ],
      [
        { id: 1, date: "2026-09-23T10:00:00Z", headline: "Q3 preview", source: "Reuters", severity: 1, href: null },
        { id: 2, date: "2026-07-01T10:00:00Z", headline: "Export licence", source: "FT", severity: 2, href: "https://x" },
      ],
    );
    expect(t.map((e) => e.key)).toEqual(["n:1", "v:2026-07-24|FY2026 Q2", "n:2"]);
    const verdict = t[1];
    expect(verdict.text).toBe("Verdict FY2026 Q2: 1 of 2 KPIs pass; China share breached (41% vs ≤ 30%).");
    expect(verdict.source).toBe("Verdict · 3 facts");
    expect(verdict.kind === "verdict" && verdict.tone).toBe("breach");
  });
});

describe("13F holder bars", () => {
  it("scales to the largest holder and reads the change as a fraction", () => {
    const rows = holderBars(
      [
        { cik: "1", manager_name: "Vanguard", shares: "200", prev_shares: "100", shares_change_pct: 1 },
        { cik: "2", manager_name: null, shares: 50, prev_shares: null, shares_change_pct: null },
      ],
      "2026-03-31",
    );
    expect(rows[0]).toMatchObject({ name: "Vanguard", share: 1, change: 1 });
    expect(rows[1]).toMatchObject({ name: "CIK 2", share: 0.25, change: "new" });
    expect(holderBars([{ cik: "2", manager_name: "X", shares: 5, prev_shares: null }], null)[0].change).toBeNull();
  });
});
