import { describe, expect, it } from "vitest";

import type { PortfolioRisk } from "@/lib/api/client";

import { formatSharpe, riskFreeShort, sharpeExplanation, sharpeLabel } from "./sharpe";

const risk = (over: Partial<PortfolioRisk> = {}, portfolio: Partial<PortfolioRisk["portfolio"]> = {}): PortfolioRisk => ({
  as_of: "2026-09-27",
  days: 365,
  base_currency: "CZK",
  method: "…",
  risk_free: {
    series_id: "czeonia",
    source: "cnb",
    label: "CZEONIA (CZK overnight rate)",
    latest_rate: 0.0356,
    latest_date: "2026-09-24",
    url: "https://www.cnb.cz/",
    fallback: false,
  },
  portfolio: {
    sharpe: 0.91,
    annualised_volatility: 0.0477,
    annualised_return: 0.075,
    mean_excess_return: 0.0434,
    risk_free_average: 0.0318,
    observations: 259,
    window_start: "2025-09-29",
    window_end: "2026-09-25",
    currency: "CZK",
    reason: null,
    ...portfolio,
  },
  benchmark_symbol: "^spx",
  benchmark: null,
  ...over,
});

describe("sharpe labels", () => {
  it("labels a full year as 1Y and a shorter window by its start", () => {
    expect(sharpeLabel(risk())).toBe("Sharpe · 1Y");
    expect(sharpeLabel(risk({}, { window_start: "2026-05-04" }))).toMatch(/^Sharpe · since /);
  });

  it("formats the ratio and the risk-free rate", () => {
    expect(formatSharpe(0.9101)).toBe("0.91");
    expect(formatSharpe(null)).toBe("—");
    expect(riskFreeShort(risk())).toBe("rf: CZEONIA 3.56%");
    expect(riskFreeShort(risk({ risk_free: null }))).toBeNull();
  });

  it("explains the method, window, source and benchmark", () => {
    const text = sharpeExplanation(
      risk({
        benchmark: {
          sharpe: 0.53,
          annualised_volatility: 0.098,
          annualised_return: 0.08,
          mean_excess_return: 0.05,
          risk_free_average: 0.03,
          observations: 259,
          window_start: "2025-09-29",
          window_end: "2026-09-25",
          currency: "CZK",
          reason: null,
        },
      }),
    );
    expect(text).toMatch(/√252/);
    expect(text).toMatch(/time-weighted/);
    expect(text).toMatch(/CZEONIA \(CZK overnight rate\) from the Czech National Bank/);
    expect(text).toMatch(/259 daily returns/);
    expect(text).toMatch(/Benchmark \^spx in CZK: Sharpe 0\.53/);
  });
});
