import { describe, expect, it } from "vitest";

import { boardVerdict, rankingVerdict, scoreExplain, weakest } from "./theses";

const kpis = (n: number) => Array.from({ length: n }, (_, i) => ({ kpi: { id: `k${i}` } })) as never[];

const row = (ticker: string, state: "intact" | "watch" | "at_risk" | "unjudged", score: number | null, counts: Record<string, number>, n = 3) => ({
  ticker,
  name: ticker === "KO" ? "Coca-Cola" : ticker,
  state,
  score,
  counts,
  kpis: kpis(n),
});

describe("thesis board verdicts", () => {
  it("explains a score from its KPI verdicts", () => {
    expect(scoreExplain(row("A", "intact", 88, { pass: 3, unknown: 1 }, 4))).toBe("3 of 4 KPIs pass; 1 without a verdict yet.");
    expect(scoreExplain(row("A", "watch", 67, { pass: 2, breach: 1 }))).toBe("2 of 3 KPIs pass; 1 breached.");
    expect(scoreExplain(row("A", "unjudged", null, {}, 0))).toBe("This thesis has no KPIs, so nothing is judged.");
  });

  it("says an all-intact board is about delivery, not price, and names the unjudged", () => {
    const v = boardVerdict([row("A", "intact", 100, { pass: 3 }), row("SXRV", "unjudged", null, {}, 0)]);
    expect(v.tone).toBe("good");
    expect(v.text).toContain("All 1 judged thesis are intact");
    expect(v.text).toContain("not that the stocks are cheap");
    expect(v.text).toContain("(SXRV) are not scored");
  });

  it("names the theses at risk and on watch", () => {
    const v = boardVerdict([row("A", "at_risk", 33, { breach: 2, pass: 1 }), row("B", "watch", 67, { breach: 1, pass: 2 }), row("C", "intact", 100, { pass: 3 })]);
    expect(v.tone).toBe("bad");
    expect(v.text).toBe("1 thesis at risk (A) and 1 on watch (B); 1 intact. A breached KPI calls for a thesis review, not an automatic sale.");
  });

  it("lists the weakest theses first with their reasons", () => {
    const out = weakest([row("A", "intact", 100, { pass: 3 }), row("KO", "intact", 50, { unknown: 4 }, 4), row("B", "intact", 88, { pass: 3, unknown: 1 }, 4)]);
    expect(out.map((w) => w.row.ticker)).toEqual(["KO", "B"]);
    expect(out[0].reason).toBe("0 of 4 KPIs pass; 4 without a verdict yet.");
  });
});

describe("ranking verdict", () => {
  const r = (name: string, total: number, meets: boolean) => ({ ticker: name, name, held: true, returns: { total, meets_hurdle: meets } as never });

  it("names who clears the hurdle and says when most of the book is fully priced", () => {
    const v = rankingVerdict([r("Nvidia", 0.33, true), r("Apple", -0.03, false), r("Coca-Cola", 0.01, false), r("Boeing", 0.22, false)], 0.3);
    expect(v?.text).toBe(
      "Only Nvidia clears your 30% return hurdle. 1 of 4 expect a loss to their own probability-weighted value and 1 expect less than 10%.",
    );
    const w = rankingVerdict([r("Apple", -0.03, false), r("Coca-Cola", 0.01, false)], 0.3);
    expect(w?.tone).toBe("caution");
    expect(w?.text).toContain("new money belongs in the core ETFs");
    expect(rankingVerdict([], 0.3)).toBeNull();
  });
});
