import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { assembleCorrelation, buildSessionCells } from "@/lib/markets/logic";
import type { CorporateAction, HoldingsPrices, PriceDiagnostic, PriceHistory, ReturnsReport } from "@/lib/markets/types";

import { ActionLedger } from "./action-ledger";
import { CorrelationPanel } from "./correlation-panel";
import { EventStudyPanel } from "./event-study-panel";
import { PortfolioCoveragePanel } from "./portfolio-coverage";
import { PriceChartPanel } from "./price-chart-panel";
import { PriceDiagnosticsPanel } from "./price-diagnostics";
import { ReturnsPanel } from "./returns-panel";
import { SessionStrip } from "./session-strip";

const bars = Array.from({ length: 40 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 7, 3 + i)).toISOString().slice(0, 10);
  return { date: d, open: 100 + i, high: 103 + i, low: 98 + i, close: 101 + i, volume: 1000 + i };
});

const prices = (over: Partial<PriceHistory> = {}): PriceHistory => ({
  ticker: "ABC",
  symbol: "abc.us",
  source: "Stooq",
  stooq_symbol: "abc.us",
  yahoo_symbol: null,
  fallback_reason: null,
  as_of: bars[bars.length - 1].date,
  fetched_at: null,
  bars,
  benchmark_symbol: null,
  benchmark: [],
  risk: null,
  correlations: [],
  gap: null,
  plausibility: null,
  ...over,
});

const diag: PriceDiagnostic = {
  symbol: "abc.us",
  source: "stooq",
  mic: "XNAS",
  currency: "USD",
  quote_unit: "USD",
  bar_type: "adjusted",
  last_session: "2026-09-11",
  observed_sessions: 38,
  disagreements: 0,
  missing_days: 2,
  access_state: "available",
  chosen: true,
  expected_sessions: 40,
  calendar_basis: "confirmed_calendar",
  stale_sessions: 0,
};

const action: CorporateAction = {
  id: 1,
  symbol: "ABC",
  type: "split",
  exDate: bars[20].date,
  recordDate: null,
  paymentDate: null,
  ratio: 4,
  cashAmount: null,
  currency: null,
  source: "Issuer",
  sourceUrl: "https://example.test",
  knownAt: null,
  evidenceId: null,
  status: "suggested",
  decidedAt: null,
  decisionReason: null,
  per: null,
  amountBasis: null,
};

const html = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);

describe("price chart panel", () => {
  it("renders with data, a marker lane and the explicit basis", () => {
    const out = html(createElement(PriceChartPanel, { ticker: "ABC", name: "Abc Inc", prices: prices(), diagnostics: [diag], actions: [action] }));
    expect(out).toContain("adjusted basis");
    expect(out).toContain("Corporate actions in the window");
    expect(out).toContain("disabled");
  });
  it("renders without bars (not covered), without diagnostics and without a ledger", () => {
    expect(html(createElement(PriceChartPanel, { ticker: "ABC", name: null, prices: prices({ bars: [], gap: "no Stooq symbol", as_of: null }), diagnostics: null, actions: null, actionsReason: "unreadable" }))).toContain("no Stooq symbol");
    const partial = html(createElement(PriceChartPanel, { ticker: "ABC", name: null, prices: prices(), diagnostics: null, actions: null, actionsReason: "unreadable" }));
    expect(partial).toContain("Corporate-action lane unavailable");
    expect(partial).toContain("Partial coverage");
  });
});

describe("ledger, returns and event study", () => {
  it("renders an empty ledger and a ledger with a suggested action", () => {
    expect(html(createElement(ActionLedger, { ticker: "ABC", actions: [] }))).toContain("No corporate action is recorded");
    const out = html(createElement(ActionLedger, { ticker: "ABC", actions: [action] }));
    expect(out).toContain("Suggested (not used in returns)");
    expect(out).toContain("no linked evidence");
    expect(out).toContain("Confirm…");
  });
  const report: ReturnsReport = {
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
      totalReturn: { value: null, reason: "corporate-action coverage is incomplete" },
      actionCoverage: "incomplete",
      coverage: 0.9,
      expectedSessions: 250,
      observedSessions: 225,
      calendarBasis: "weekdays_minus_configured_closures",
      missingActions: { priceRaw: [], priceAdjusted: [], totalReturn: ["split on 2026-09-10 is not valued"] },
      start: "2025-10-03",
      end: "2026-10-03",
    },
  };
  it("shows each basis, the reason total return is withheld and never a zero", () => {
    const out = html(createElement(ReturnsPanel, { report }));
    expect(out).toContain("Raw price return");
    expect(out).toContain("unavailable");
    expect(out).toContain("corporate-action coverage is incomplete");
    expect(html(createElement(ReturnsPanel, { report: { ...report, result: null, reason: "no stored price series with a known quote currency" } }))).toContain("no stored price series");
  });
  it("replaces the event study with the gate's reason when not reconciled, and draws it when allowed", () => {
    const blocked = html(createElement(EventStudyPanel, { ticker: "ABC", source: "Stooq", bars, actions: [action], gate: { allowed: false, reason: "coverage not verified" } }));
    expect(blocked).toContain("coverage not verified");
    expect(blocked).not.toContain("Indexed price path");
    const confirmed = { ...action, status: "confirmed" as const, exDate: bars[20].date };
    const open = html(createElement(EventStudyPanel, { ticker: "ABC", source: "Stooq", bars, actions: [confirmed], gate: { allowed: true } }));
    expect(open).toContain("1 confirmed event");
    expect(open).toContain("Indexed price path");
    expect(html(createElement(EventStudyPanel, { ticker: "ABC", source: "Stooq", bars, actions: [], gate: { allowed: true } }))).toContain("Not covered");
  });
});

describe("diagnostics and sessions", () => {
  it("renders diagnostics with rows, with none, and a missing listing note", () => {
    const rows = html(createElement(PriceDiagnosticsPanel, { ticker: "ABC", rows: [diag, { ...diag, chosen: false, bar_type: "raw", source: "yahoo" }], listing: null, listingNote: "No listing has been derived" }));
    expect(rows).toContain("SERVED");
    expect(rows).toContain("No listing has been derived");
    expect(html(createElement(PriceDiagnosticsPanel, { ticker: "ABC", rows: [], listing: null }))).toContain("No price series is stored");
  });
  it("renders the strip with a missing bar and an empty window", () => {
    const days = ["2026-09-28", "2026-09-29", "2026-09-30"];
    const cells = buildSessionCells({
      mic: "XNAS",
      start: "2026-09-28",
      end: "2026-09-30",
      today: "2026-10-05",
      expected: { mic: "XNAS", start: "2026-09-28", end: "2026-09-30", count: 3, basis: "weekdays_minus_configured_closures", confirmedDays: 0, days },
      sessions: [],
      calendar: { closures: [], suggestions: [] },
      barDates: ["2026-09-28", "2026-09-30"],
      firstBar: "2026-09-01",
    });
    const out = html(createElement(SessionStrip, { ticker: "ABC", mic: "XNAS", cells, basis: "weekdays_minus_configured_closures", confirmedDays: 0, suggestionsNote: "n", asOf: "2026-09-30" }));
    expect(out).toContain("Missing bar");
    expect(out).toContain("1 expected session without a bar");
    expect(html(createElement(SessionStrip, { ticker: "ABC", mic: "XNAS", cells: [], basis: "confirmed_calendar", confirmedDays: 0, suggestionsNote: "n", asOf: null }))).toContain("Not covered");
  });
});

describe("correlation and portfolio coverage", () => {
  it("renders a matrix, names the missing counts endpoint, and handles fewer than two holdings", () => {
    const grid = assembleCorrelation([
      { ticker: "A", correlations: [{ ticker: "B", correlation: 0.7 }] },
      { ticker: "B", correlations: [{ ticker: "A", correlation: 0.7 }] },
    ]);
    const out = html(createElement(CorrelationPanel, { grid, names: { A: "Alpha" }, asOf: "2026-10-02", source: "Stooq", failed: [], risk: null }));
    expect(out).toContain("Alpha");
    expect(out).toContain("shared observations");
    expect(out).toContain("0.70");
    const one = html(createElement(CorrelationPanel, { grid: assembleCorrelation([{ ticker: "A", correlations: null }]), names: {}, asOf: null, source: null, failed: ["Z"], risk: null }));
    expect(one).toContain("Fewer than two holdings");
  });
  it("renders portfolio coverage with a holding that has no bars, and with no holdings", () => {
    const data: HoldingsPrices = {
      days: 30,
      holdings: [
        { ticker: "A", name: null, symbol: "a.us", source: "Stooq", as_of: "2026-10-02", last_close: 10, previous_close: 9, day_change_pct: 0.111, bars },
        { ticker: "B", name: null, symbol: null, source: null, as_of: null, last_close: null, previous_close: null, day_change_pct: null, bars: [] },
      ],
    };
    const out = html(createElement(PortfolioCoveragePanel, { data }));
    expect(out).toContain("No stored bars");
    expect(out).toContain("1 of 2 holdings");
    // The backend sends a fraction: 0.111 is +11.1 %, not +0.11 %.
    expect(out).toContain("+11.10%");
    expect(out).not.toContain("+0.11%");
    // Last close carries its quote unit; unknown stays honest.
    const withQuote = html(createElement(PortfolioCoveragePanel, { data, quotes: { A: "GBX" } }));
    expect(withQuote).toContain("10 GBX");
    expect(out).toContain("(unit not reported)");
    expect(html(createElement(PortfolioCoveragePanel, { data: { days: 30, holdings: [] } }))).toContain("Not covered");
  });
});

describe("audit F3 regressions", () => {
  it("labels the correlation scale -1..+1, not percent", () => {
    const grid = assembleCorrelation([
      { ticker: "A", correlations: [{ ticker: "B", correlation: 0.7 }] },
      { ticker: "B", correlations: [{ ticker: "A", correlation: 0.7 }] },
    ]);
    const out = html(createElement(CorrelationPanel, { grid, names: {}, asOf: null, source: null, failed: [], risk: null }));
    expect(out).not.toContain("−100%");
    expect(out).not.toContain("+100%");
    expect(out).toContain('<span class="num">−1</span>');
    expect(out).toContain('<span class="num">+1</span>');
  });
  it("gives the raw price return the backend's price_raw reason, not 'No reason given'", () => {
    const base: ReturnsReport = {
      ticker: "ABC",
      knownAsOf: "2026-10-03",
      reason: null,
      result: {
        symbol: "abc.us", source: "stooq", currency: "USD", mic: "XNAS",
        priceReturn: { value: 0.1, reason: null },
        rawPriceReturn: { value: null, reason: "no raw bars stored for abc.us" },
        totalReturn: { value: null, reason: "split not valued" },
        actionCoverage: "available", coverage: 1, expectedSessions: 10, observedSessions: 10, calendarBasis: null,
        missingActions: { priceRaw: [], priceAdjusted: [], totalReturn: [] }, start: "2026-09-01", end: "2026-10-03",
      },
    };
    const out = html(createElement(ReturnsPanel, { report: base }));
    expect(out).toContain("no raw bars stored for abc.us");
    expect(out).not.toContain("No reason given by the backend");
  });
  it("ignores a javascript: source URL in the action ledger", () => {
    const out = html(createElement(ActionLedger, { ticker: "ABC", actions: [{ ...action, sourceUrl: "javascript:alert(1)" }] }));
    expect(out).not.toContain("javascript:");
  });
  it("draws a stale (not collected yet) session distinctly from a missing bar", () => {
    const days = ["2026-09-28", "2026-09-29", "2026-09-30"];
    const cells = buildSessionCells({
      mic: "XNAS", start: "2026-09-28", end: "2026-09-30", today: "2026-10-05",
      expected: { mic: "XNAS", start: "2026-09-28", end: "2026-09-30", count: 3, basis: "confirmed_calendar", confirmedDays: 3, days },
      sessions: [], calendar: { closures: [], suggestions: [] }, barDates: ["2026-09-28"], firstBar: "2026-09-01",
    });
    const out = html(createElement(SessionStrip, { ticker: "ABC", mic: "XNAS", cells, basis: "confirmed_calendar", confirmedDays: 3, suggestionsNote: "n", asOf: "2026-09-28" }));
    expect(out).toContain("Not collected yet");
    expect(out).not.toContain("without a bar (");
    expect(out).toContain("1 of 1 expected sessions held");
  });
});
