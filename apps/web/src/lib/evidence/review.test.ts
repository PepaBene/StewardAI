import { describe, expect, it } from "vitest";

import type { JobSummary, KpiStatus, ThesisKPI, VerdictOut } from "@/lib/api/client";

import {
  affectedKpis,
  allowedReviewActions,
  apiErrorDetail,
  describeReviewFailure,
  formatInFactScale,
  isHeld,
  kpiStatusSnapshot,
  metricsRelated,
  parseCorrectedValue,
  parseReviewTicker,
  REASON_MAX,
  reviewQueueHref,
  scaleHint,
  unitsToPrinted,
  validateReview,
  verdictRunOutcome,
} from "./review";

const money = { scale: "millions", unit: "currency", currency: "USD", value_as_written: "94,036" } as const;

function job(id: number, status: JobSummary["status"]): JobSummary {
  return {
    id,
    job_type: "thesis_verdicts",
    trigger: "manual",
    status,
    error: null,
    created_at: "2026-09-25T10:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

function kpi(id: string, metric: string): ThesisKPI {
  return { id, metric, operator: ">=", threshold: 1, unit: "%", basis: "reported", period: "quarter", description: "" };
}

function verdict(kpiId: string, status: VerdictOut["status"], factIds: number[] = []): VerdictOut {
  return {
    id: 1,
    kpi_id: kpiId,
    period: "Q2 FY2026",
    fiscal_year: 2026,
    fiscal_period: "Q2",
    status,
    observed_value: 1,
    operator: ">=",
    threshold: 1,
    threshold_unit: "%",
    rationale: "",
    fact_ids: factIds,
    config_version_id: 1,
    created_at: "2026-09-25T10:00:00Z",
    confidence: "verified",
    stale: false,
  };
}

describe("review actions", () => {
  it("offers what the API allows for each status", () => {
    expect(allowedReviewActions("unverified")).toEqual(["accept", "correct", "reject"]);
    expect(allowedReviewActions("quarantined")).toEqual(["accept", "correct", "reject"]);
    expect(allowedReviewActions("verified")).toEqual(["correct", "reject"]);
    expect(allowedReviewActions("rejected")).toEqual([]);
    expect(isHeld("quarantined")).toBe(true);
    expect(isHeld("verified")).toBe(false);
  });
});

describe("corrected values", () => {
  it("parses plain and thousands-grouped numbers into exact decimal strings", () => {
    expect(parseCorrectedValue("94036")).toBe("94036");
    expect(parseCorrectedValue(" 94,036.50 ")).toBe("94036.5");
    expect(parseCorrectedValue("-1.20")).toBe("-1.2");
    expect(parseCorrectedValue(".5")).toBe("0.5");
    expect(parseCorrectedValue("007")).toBe("7");
    expect(parseCorrectedValue("-0")).toBe("0");
  });

  it("rejects anything ambiguous", () => {
    for (const bad of ["", "abc", "1,2", "94.036,5", "1e6", "12%", "1.2.3", "--1"]) {
      expect(parseCorrectedValue(bad)).toBeNull();
    }
  });
});

describe("validateReview", () => {
  it("requires an action and a reason of 1 to 500 characters", () => {
    expect(validateReview({ action: null, reason: "", value: "" })).toEqual({
      ok: false,
      errors: { action: expect.any(String), reason: expect.any(String) },
    });
    const long = validateReview({ action: "accept", reason: "x".repeat(REASON_MAX + 1), value: "" });
    expect(long.ok).toBe(false);
    expect(validateReview({ action: "accept", reason: "  x ".repeat(1), value: "" })).toEqual({
      ok: true,
      body: { action: "accept", reason: "x" },
    });
  });

  it("requires a number for a correction and sends it as an exact string", () => {
    expect(validateReview({ action: "correct", reason: "typo", value: "" })).toMatchObject({
      ok: false,
      errors: { value: "Enter the right value." },
    });
    expect(validateReview({ action: "correct", reason: "typo", value: "9x" })).toMatchObject({ ok: false });
    expect(validateReview({ action: "correct", reason: "typo", value: "94,036" })).toEqual({
      ok: true,
      body: { action: "correct", reason: "typo", value: "94036" },
    });
  });

  it("never sends a value with accept or reject", () => {
    expect(validateReview({ action: "reject", reason: "wrong", value: "12" })).toEqual({
      ok: true,
      body: { action: "reject", reason: "wrong" },
    });
  });
});

describe("scale", () => {
  it("explains the printed scale for a correction", () => {
    expect(scaleHint(money)).toBe("Type the value in millions of USD, as printed (not in units): 94,036 means USD 94,036 m.");
    expect(scaleHint({ ...money, scale: "units", value_as_written: "12" })).toBe("Type the value in USD, as printed: 12 means USD 12.");
    expect(scaleHint({ scale: "units", unit: "percent", currency: null, value_as_written: "46.5" })).toBe(
      "Type the percentage without the % sign, as printed: 46.5 means 46.5%.",
    );
    expect(scaleHint({ scale: "units", unit: "per_share", currency: "USD", value_as_written: "1.57" })).toMatch(/per share in USD/);
  });

  it("writes XBRL values (in units) in the fact's printed scale", () => {
    expect(unitsToPrinted(94_036_000_000, "millions")).toBe(94_036);
    expect(formatInFactScale(94_036_000_000, money, { locale: "en-US" })).toBe("USD 94,036 m");
    expect(formatInFactScale(-2_500_000, money, { locale: "en-US", signed: true })).toBe("USD -2.5 m");
    expect(formatInFactScale(null, money)).toBe("—");
  });
});

describe("queue filter", () => {
  it("reads a valid ticker from the URL and builds links", () => {
    expect(parseReviewTicker(new URLSearchParams("ticker=AAPL_US_EQ"))).toBe("AAPL_US_EQ");
    expect(parseReviewTicker(new URLSearchParams("ticker=a%20b"))).toBeNull();
    expect(parseReviewTicker(new URLSearchParams(""))).toBeNull();
    expect(reviewQueueHref("BRK.B_US_EQ")).toBe("/review?ticker=BRK.B_US_EQ");
    expect(reviewQueueHref(null)).toBe("/review");
  });
});

describe("verdict run after a review", () => {
  it("waits for the queued run, or a newer one, to finish", () => {
    expect(verdictRunOutcome(null, 5)).toBe("pending");
    expect(verdictRunOutcome(job(4, "succeeded"), 5)).toBe("pending");
    expect(verdictRunOutcome(job(5, "running"), 5)).toBe("pending");
    expect(verdictRunOutcome(job(5, "succeeded"), 5)).toBe("succeeded");
    expect(verdictRunOutcome(job(5, "failed"), 5)).toBe("failed");
    expect(verdictRunOutcome(job(6, "succeeded"), 5)).toBe("succeeded");
    expect(verdictRunOutcome(job(6, "queued"), 5)).toBe("pending");
  });
});

describe("affected KPIs", () => {
  const kpis: KpiStatus[] = [
    { kpi: kpi("growth", "revenue_growth_yoy"), current: verdict("growth", "pass"), history: [] },
    { kpi: kpi("margin", "gross_margin"), current: verdict("margin", "unknown", [42]), history: [] },
    { kpi: kpi("fcf", "fcf"), current: null, history: [] },
  ];

  it("relates metrics that are the same figure or derive from it", () => {
    expect(metricsRelated("revenue_growth_yoy", "revenue")).toBe(true);
    expect(metricsRelated("revenue", "revenue")).toBe(true);
    expect(metricsRelated("revenue", "revenue_segment")).toBe(true);
    expect(metricsRelated("gross_margin", "revenue")).toBe(false);
  });

  it("keeps KPIs by metric, by the facts their verdict uses, and by a changed status", () => {
    expect(affectedKpis(kpis, { metric: "revenue", ids: [1] }).map((k) => k.kpi.id)).toEqual(["growth"]);
    expect(affectedKpis(kpis, { metric: "eps", ids: [42] }).map((k) => k.kpi.id)).toEqual(["margin"]);
    const before = new Map([
      ["growth", "pass" as const],
      ["margin", "unknown" as const],
      ["fcf", "breach" as const],
    ]);
    const affected = affectedKpis(kpis, { metric: "eps", ids: [] }, before);
    expect(affected.map((k) => [k.kpi.id, k.before, k.changed])).toEqual([["fcf", "breach", true]]);
  });

  it("shows every KPI when none relates, so the recomputed state is always visible", () => {
    const all = affectedKpis(kpis, { metric: "eps", ids: [] }, kpiStatusSnapshot(kpis));
    expect(all.map((k) => k.kpi.id)).toEqual(["growth", "margin", "fcf"]);
    expect(all.every((k) => !k.changed)).toBe(true);
    expect(kpiStatusSnapshot(kpis).get("fcf")).toBeNull();
  });
});

describe("review errors", () => {
  it("explains 409, 404 and 422 answers", () => {
    expect(apiErrorDetail({ detail: [{ msg: "too long" }, { msg: "bad" }] })).toBe("too long; bad");
    expect(describeReviewFailure(409, { detail: "Fact 3 is verified; it cannot be accepted" })).toBe(
      "Not allowed for this figure now: Fact 3 is verified; it cannot be accepted. Reload to see its current status.",
    );
    expect(describeReviewFailure(404, { detail: "No fact 3" })).toBe("This figure no longer exists (No fact 3).");
    expect(describeReviewFailure(500, undefined)).toBe("The API answered HTTP 500.");
  });
});
