import { describe, expect, it } from "vitest";

import { aggregateProvenance, buildExposureTreemap, buildLookThroughSankey, treemapNotes, usableOverlap } from "./exposure";
import { auditExplanation, normaliseSubjectKey, overrideNote, removeOverrideNote, subjectKeyProblem } from "./funds";
import * as insiderModule from "./insider";
import {
  classTotals,
  countsTowardNetText,
  insiderProvenance,
  netValueText,
  pdmrHoldingState,
} from "./insider";
import { breadthRows, stakePercentNote } from "./ownership";
import { parseAudit } from "./parse";
import type { AggregateReport, ExposureBucket, FundAsOf, FundOverlapReport, InsiderActivityOut, InsiderTransactionRow, IssuerExposure, HoldingPdmrOut, OwnershipPeriod } from "./types";
import { validAsOf } from "./view";

const bucket = (key: string, kind: ExposureBucket["kind"], weight: number, direct = 0): ExposureBucket => ({ key, label: key, kind, weight, value_base: weight * 1000, direct_weight: direct, fund_weight: weight - direct });
const issuer = (key: string, weight: number, over: Partial<IssuerExposure> = {}): IssuerExposure =>
  ({ key, label: key, merged_securities: 1, securities: [], weight, value_base: weight * 1000, direct_weight: 0, fund_weight: weight, overlap_weight: 0, channels: [], sector: "Technology", country: "US", region: "NA", currency: "USD", ...over }) as IssuerExposure;
const fund = (isin: string, weight: number, over: Partial<FundAsOf> = {}): FundAsOf =>
  ({ isin, label: isin, ticker: isin.slice(0, 4), weight, value_base: weight * 1000, status: "ok", holdings_as_of: "2026-09-01", lag_days: 10, stale: false, source: "s", rows_total_weight: 1, residual_weight: 0, unknown_weight: 0, reconciles: true, expense_ratio: null, fee_drag: null, warnings: [], ...over }) as FundAsOf;
const report = (over: Partial<AggregateReport> = {}): AggregateReport =>
  ({ as_of: "2026-10-03", portfolio_snapshot_date: "2026-10-02", base_currency: "EUR", total_value: 1000, available: true, method: "m", funds: [], stale_funds: [], issuers: [], overlap: [], exposures: [], fees: null, unknown: null, notes: [], ...over }) as AggregateReport;

describe("F6-09 aggregate provenance", () => {
  it("an over-reported fund is not verified", () => {
    const p = aggregateProvenance(report({ funds: [fund("IE00AAAAAAA1", 0.5, { rows_total_weight: 1.1, reconciles: false })], unknown: { unknown_fund_weight: 0, unknown_fund_value: 0, not_looked_through_weight: 0, residual_weight: 0, over_reported_weight: 0.05, by_fund: {} } }));
    expect(p.state).toBe("partial");
    expect(p.reason).toContain("more than 100%");
  });
  it("a stale fund does not hide unknown weight", () => {
    const p = aggregateProvenance(report({ funds: [fund("IE00AAAAAAA1", 0.4, { stale: true, status: "stale" }), fund("IE00BBBBBBB2", 0.3, { status: "missing", unknown_weight: 0.3 })], stale_funds: ["A"], unknown: { unknown_fund_weight: 0.3, unknown_fund_value: 300, not_looked_through_weight: 0.3, residual_weight: 0, over_reported_weight: 0, by_fund: {} } }));
    expect(p.state).toBe("stale");
    expect(p.reason).toContain("Stale fund holdings");
    expect(p.reason).toContain("30.0% of portfolio value is unknown");
  });
});

describe("F6-02 treemap issuer split across buckets", () => {
  it("draws the issuer in each bucket with its own weight and a remainder", () => {
    const t = buildExposureTreemap(
      report({
        issuers: [issuer("I", 0.3)],
        exposures: [{ dimension: "sector", reconciles: true, issuer_buckets: { I: { Technology: 0.1, "Information Technology": 0.2 } }, buckets: [bucket("Technology", "classified", 0.15, 0.15), bucket("Information Technology", "classified", 0.2)] }],
      }),
      "sector",
    )!;
    const tech = t.groups.find((g) => g.key === "Technology")!;
    const it = t.groups.find((g) => g.key === "Information Technology")!;
    expect(tech.children.map((c) => [c.kind, c.weight])).toEqual([["issuer", 0.1], ["remainder", expect.closeTo(0.05)]]);
    expect(it.children.map((c) => c.kind)).toEqual(["issuer"]);
    expect(it.children[0].weight).toBeCloseTo(0.2);
    expect(tech.children.reduce((s, c) => s + c.weight, 0)).toBeCloseTo(tech.weight);
    expect(tech.overflow).toBe(false);
    expect(tech.children[0].detail).toContain("issuer total 30.00%");
  });
  it("has no issuer leaves when the backend lists none for a bucket", () => {
    const t = buildExposureTreemap(report({ issuers: [issuer("I", 0.3)], exposures: [{ dimension: "sector", reconciles: true, issuer_buckets: {}, buckets: [bucket("Technology", "classified", 0.3)] }] }), "sector")!;
    expect(t.groups[0].children.map((c) => c.kind)).toEqual(["remainder"]);
  });
});

describe("F6-03 negative fund cash bucket", () => {
  it("explains the dropped bucket and the drawn total", () => {
    const t = buildExposureTreemap(
      report({ issuers: [], exposures: [{ dimension: "sector", reconciles: true, buckets: [bucket("A", "classified", 1.02), bucket("__fund_cash__", "fund_cash", -0.02)] }] }),
      "sector",
    )!;
    const notes = treemapNotes(t);
    expect(notes.some((n) => n.includes("-2.00%") && n.includes("negative offset"))).toBe(true);
    expect(notes.some((n) => n.includes("102.0%"))).toBe(true);
  });
  it("is silent when everything adds up", () => {
    const t = buildExposureTreemap(report({ exposures: [{ dimension: "sector", reconciles: true, buckets: [bucket("A", "classified", 1)] }] }), "sector")!;
    expect(treemapNotes(t)).toEqual([]);
  });
});

describe("F6-04 sankey with one fund ISIN on two listings", () => {
  it("conserves flow through the fund node", () => {
    const isin = "IE00AAAAAAA1";
    const ch = (w: number) => ({ kind: "fund", ticker: "A", isin, label: "A", weight: w, value_base: 0 }) as IssuerExposure["channels"][number];
    const s = buildLookThroughSankey(
      report({
        funds: [fund(isin, 0.3, { ticker: "VUSAl_EQ" }), fund(isin, 0.2, { ticker: "VUSA_EQ" })],
        issuers: [issuer("I", 0.4, { channels: [ch(0.24), ch(0.16)] })],
        exposures: [{ dimension: "sector", reconciles: true, buckets: [] }],
      }),
      null,
    )!;
    const into = s.links.filter((l) => l.target === `src:${isin}`).reduce((a, l) => a + l.value, 0);
    const out = s.links.filter((l) => l.source === `src:${isin}`).reduce((a, l) => a + l.value, 0);
    expect(into).toBeCloseTo(0.5);
    expect(out).toBeCloseTo(0.5);
  });
});

describe("F6-01 overlap from another as-of date", () => {
  it("is not used for this date's aggregate", () => {
    const o = { as_of: "2026-09-01" } as FundOverlapReport;
    expect(usableOverlap({ as_of: "2026-10-03" }, o)).toBeNull();
    expect(usableOverlap({ as_of: "2026-09-01" }, o)).toBe(o);
  });
});

const tx = (cls: string, shares: number, over: Partial<InsiderTransactionRow> = {}) =>
  ({ economic_class: cls, shares, superseded_by: null, derivative: false, counts_toward_net_discretionary: true, exclusion_reason: null, ...over }) as unknown as InsiderTransactionRow;

describe("F6-08 insider class totals", () => {
  it("does not say a 10b5-1 sale counts toward net", () => {
    const { totals } = classTotals([tx("open_market_sell", 50000, { counts_toward_net_discretionary: false, exclusion_reason: "pre_planned_10b5_1" }), tx("open_market_sell", 2000)]);
    const t = totals[0];
    expect(t.countedRows).toBe(1);
    expect(countsTowardNetText(t)).toBe("1 of 2 rows");
    expect(t.exclusions).toEqual([{ reason: "pre_planned_10b5_1", rows: 1 }]);
  });
  it("counts an exercise once, not once per leg", () => {
    const { totals } = classTotals([tx("option_exercise", 10000, { derivative: true, counts_toward_net_discretionary: false }), tx("option_exercise", 10000)]);
    expect(totals[0].shares).toBe(10000);
    expect(totals[0].derivativeLegRows).toBe(1);
  });
});

const activity = (over: Record<string, unknown> = {}, rows: InsiderTransactionRow[] = []) =>
  ({ transactions: rows, net_discretionary: { since: "2026-04-03", until: "2026-10-03", buy_rows: 0, sell_rows: 0, net_value: null, value_complete: true, plan_status_unknown_rows: 0, ...over } }) as unknown as InsiderActivityOut;

describe("F6-11 insider freshness", () => {
  it("uses the newest filing, not the window end, and flags a quiet window", () => {
    const p = insiderProvenance(activity({}, [tx("open_market_buy", 1, { filed: "2026-03-01" } as never)]), { lastGood: false, rowLimit: 200 });
    expect(p.asOf).toBe("2026-03-01");
    expect(p.state).toBe("stale");
  });
  it("says no row counted instead of blaming prices", () => {
    expect(netValueText(activity().net_discretionary, "x")).toContain("no row counted");
    expect(netValueText(activity({ buy_rows: 1 }).net_discretionary, "x")).toContain("no price");
  });
});

describe("F6-05 PDMR state is per holding", () => {
  const out = (notifications: unknown[], status: Partial<HoldingPdmrOut["status"]> = {}) =>
    ({ notifications, status: { state: "available", detail: null, parsed: 47, attempted: 52, by_status: {}, ...status } }) as unknown as HoldingPdmrOut;
  it("a holding without notifications is not verified from channel-wide counts", () => {
    const h = pdmrHoldingState(out([]));
    expect(h.state).toBe("not_covered");
    expect(h.total).toBe(0);
  });
  it("one unreadable announcement makes the holding partial", () => {
    expect(pdmrHoldingState(out([{ status: "parsed" }, { status: "no_template" }])).state).toBe("partial");
    expect(pdmrHoldingState(out([{ status: "parsed" }])).state).toBe("verified");
  });
  it("uses ticker_status counts for the holding, not the channel-wide status", () => {
    const withTs = (ts: unknown, n: unknown[] = []) => ({ ...out(n), ticker_status: ts }) as unknown as HoldingPdmrOut;
    const h = pdmrHoldingState(withTs({ state: "available", detail: null, parsed: 3, attempted: 4, by_status: { parsed: 3, no_template: 1 } }, [{ status: "parsed" }]));
    expect(h).toMatchObject({ state: "partial", parsed: 3, total: 4 });
    expect(pdmrHoldingState(withTs({ state: "available", detail: null, parsed: 2, attempted: 2, by_status: {} })).state).toBe("verified");
    const none = pdmrHoldingState(withTs({ state: "access_unverified", detail: null, parsed: 0, attempted: 0, by_status: {} }));
    expect(none).toMatchObject({ state: "not_covered", total: 0 });
  });
  it("has no lowercase-ticker caveat any more", () => {
    expect(insiderModule).not.toHaveProperty("tickerCaseCaveat");
  });
});

describe("F6-07 13D/G percent wording", () => {
  it("states the largest-single-person rule for multiple reporting persons", () => {
    const n = stakePercentNote({ isGroup: true, ownersCount: 2 })!;
    expect(n).toContain("largest single reporting person");
    expect(n).toContain("at least this");
    expect(n).not.toContain("percent is the group's");
    expect(stakePercentNote({ isGroup: false, ownersCount: 1 })).toBeNull();
  });
});

describe("F6-14 13F change span", () => {
  it("labels the comparison quarter from the API previous_period, the oldest row has none", () => {
    const p = (period: string, previous_period: string | null, holders_change: number | null) => ({ period, holders: 3, shares: "10", value_usd: "1", latest_filed: null, holders_change, shares_change: null, previous_period }) as unknown as OwnershipPeriod;
    const rows = breadthRows([p("2026-06-30", "2025-12-31", 1), p("2025-12-31", null, null)]);
    expect(rows[0].previousPeriod).toBeNull();
    expect(rows[0]).not.toHaveProperty("changeSpanUnknown");
    expect(rows[1].previousPeriod).toBe("2025-12-31");
    expect(rows[1].quartersSincePrevious).toBe(2);
  });
});

describe("F6-12 as_of validation", () => {
  const today = new Date("2026-10-03T12:00:00Z");
  it("drops garbage, impossible and future dates", () => {
    expect(validAsOf("garbage", today)).toBe("");
    expect(validAsOf("2026-02-30", today)).toBe("");
    expect(validAsOf("2027-01-01", today)).toBe("");
    expect(validAsOf("2026-09-01", today)).toBe("2026-09-01");
    expect(validAsOf(null, today)).toBe("");
  });
});

describe("F6-10 manual ledger honesty", () => {
  const rows = [{ subject_kind: "index" as const, subject_key: "SPX" }];
  it("upper-cases fund ISINs and reuses an existing index key's spelling", () => {
    expect(normaliseSubjectKey("fund", " ie00b5bmr087 ", [])).toBe("IE00B5BMR087");
    expect(normaliseSubjectKey("index", "spx", rows)).toBe("SPX");
    expect(subjectKeyProblem("fund", "abc")).toContain("ISIN");
    expect(subjectKeyProblem("fund", "IE00B5BMR087")).toBeNull();
  });
  it("says a derived row is overridden and its as-of cleared", () => {
    const note = overrideNote({ subject_key: "IE00B5BMR087", origin: "derived", tier: "issuer_downloadable", latest_holdings_as_of: "2026-09-01" })!;
    expect(note).toContain("overrides the derived row");
    expect(note).toContain("holdings as-of date is cleared");
    expect(note).not.toContain("no way to revert");
    expect(note).toContain("restore the derived row");
    expect(overrideNote(undefined)).toBeNull();
  });
});

describe("coverage ledger manual override removal", () => {
  it("says the derived row is restored and its as-of recomputed", () => {
    const t = removeOverrideNote({ subject_key: "IE00B5BMR087" });
    expect(t).toContain("restores the derived row");
    expect(t).toContain("recomputed");
  });
});

describe("F6-13 audit explanation", () => {
  it("keeps previous rules and the as-of flags", () => {
    const parsed = parseAudit([
      { fund_key: "F", as_of: "2026-09-01", verdict: "methodology_change", alert: true, rules: { cap: 5 }, detail: { previous_rules: { cap: 10 } } },
      { fund_key: "F", as_of: "2026-08-01", verdict: "value_change", alert: true, rules: {}, detail: { as_of_moved_backwards: true, changes: {} } },
    ])!;
    expect(auditExplanation(parsed.rows[0]).join(" ")).toContain("cap: 10");
    expect(auditExplanation(parsed.rows[1]).join(" ")).toContain("moved backwards");
  });
});
