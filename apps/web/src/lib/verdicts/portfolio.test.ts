import { describe, expect, it } from "vitest";

import {
  benchmarkVerdict,
  concentrationVerdict,
  coverageVerdict,
  incomeChangeVerdict,
  overvaluedWeightVerdict,
  staleVerdict,
  timingVerdict,
  unrealisedVerdict,
  valueGapVerdict,
} from "./portfolio";

describe("portfolio verdicts", () => {
  it("compares a return with the benchmark in points", () => {
    expect(benchmarkVerdict(0.12, 0.1, "S&P 500")).toEqual({ tone: "good", text: "2.0 points ahead of S&P 500." });
    expect(benchmarkVerdict(0.05, 0.1).tone).toBe("bad");
    expect(benchmarkVerdict(0.1, 0.102).tone).toBe("neutral");
    expect(benchmarkVerdict(null, 0.1).tone).toBe("unknown");
    expect(benchmarkVerdict(0.1, null).tone).toBe("neutral");
  });

  it("explains a holdings return and a money return that disagree", () => {
    expect(timingVerdict(0.127, -0.051, "Since the first snapshot")?.text).toBe(
      "Since the first snapshot: the holdings returned +12.7%, your money −5.1%. The holdings did best while little of your money was in them; most of it arrived later and has done worse.",
    );
    expect(timingVerdict(0.02, 0.08, "1 month")?.text).toContain("more than the holdings' +2.0%");
    expect(timingVerdict(0.071, 0.06, "1 month")).toBeNull();
    expect(timingVerdict(null, 0.06, "1 month")).toBeNull();
  });

  it("states unrealised result against cost", () => {
    expect(unrealisedVerdict(0.123).text).toBe("Open positions are worth 12.3% more than you paid.");
    expect(unrealisedVerdict(-0.05).tone).toBe("bad");
    expect(unrealisedVerdict(null).tone).toBe("unknown");
  });

  it("compares income with the trailing year", () => {
    expect(incomeChangeVerdict(0.051).text).toBe("5.1% more than the last 12 months.");
    expect(incomeChangeVerdict(-0.1).tone).toBe("caution");
    expect(incomeChangeVerdict(null).tone).toBe("unknown");
    expect(incomeChangeVerdict(14.526)).toEqual({ tone: "neutral", text: "Mostly from positions held under a year, so the last 12 months are no fair comparison." });
  });

  it("scores coverage by share of holdings", () => {
    expect(coverageVerdict(5, 30)).toEqual({ tone: "bad", text: "5 of 30 holdings have a fair value on this basis." });
    expect(coverageVerdict(1, 1).text).toBe("1 of 1 holding has a fair value on this basis.");
    expect(coverageVerdict(24, 30).tone).toBe("good");
    expect(coverageVerdict(15, 30).tone).toBe("caution");
    expect(coverageVerdict(0, 0).tone).toBe("unknown");
  });

  it("reads the fair-value gap and overvalued weight", () => {
    expect(valueGapVerdict(0.3).tone).toBe("good");
    expect(valueGapVerdict(-0.3).text).toBe("Covered holdings trade 30% above fair value.");
    expect(valueGapVerdict(0.05).tone).toBe("neutral");
    expect(valueGapVerdict(null).tone).toBe("unknown");
    expect(overvaluedWeightVerdict(0.6).tone).toBe("bad");
    expect(overvaluedWeightVerdict(0.3).tone).toBe("caution");
    expect(overvaluedWeightVerdict(0.1).tone).toBe("good");
  });

  it("flags stale fair values", () => {
    expect(staleVerdict(0).tone).toBe("good");
    expect(staleVerdict(1).text).toBe("1 fair value is too old to trust until refreshed.");
    expect(staleVerdict(3).text).toBe("3 fair values are too old to trust until refreshed.");
  });

  it("reads concentration from the effective number of holdings", () => {
    expect(concentrationVerdict(null, 0).tone).toBe("unknown");
    expect(concentrationVerdict(3.2, 12)).toEqual({ tone: "bad", text: "Your 12 holdings behave like 3.2 equal ones: a few positions decide the result." });
    expect(concentrationVerdict(7, 12).tone).toBe("caution");
    expect(concentrationVerdict(14.5, 30).tone).toBe("good");
  });
});
