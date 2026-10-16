import { describe, expect, it } from "vitest";

import {
  CONFIDENCE,
  confidenceLabel,
  confidenceNextAction,
  confidenceReason,
  EVIDENCE_RELATION,
  evidenceRelationLabel,
  formatFactValue,
  formatObserved,
  formatTarget,
  humanizeMetric,
  metricLabel,
  isLongStandingVerified,
  tallyConfidence,
  tallyConfidenceLabel,
  VERDICT_STATUS,
  VERIFIED_QUIET_AFTER_DAYS,
} from "./confidence";

describe("confidence labels", () => {
  it("has exactly the four labels of the spec, none using gain or loss colours", () => {
    expect(Object.values(CONFIDENCE).map((c) => c.label)).toEqual([
      "Verified",
      "Corroborated",
      "Unconfirmed",
      "Disputed",
    ]);
    for (const c of Object.values(CONFIDENCE)) {
      expect(["gain", "loss"]).not.toContain(c.tone);
    }
    expect(confidenceLabel("unconfirmed")).toBe("Unconfirmed");
  });

  it("gives evidence relation its own diverging cool/neutral/warm tones, never confidence hues", () => {
    expect(Object.values(EVIDENCE_RELATION).map((r) => r.label)).toEqual(["Supports", "Mentions", "Contradicts"]);
    expect(EVIDENCE_RELATION.supports.tone).toBe("diverging-under");
    expect(EVIDENCE_RELATION.mentions.tone).toBe("diverging-neutral");
    expect(EVIDENCE_RELATION.contradicts.tone).toBe("diverging-over");
    expect(evidenceRelationLabel("contradicts")).toBe("Contradicts");
  });

  it("styles unknown apart from pass and breach", () => {
    expect(VERDICT_STATUS.pass.tone).toBe("gain");
    expect(VERDICT_STATUS.breach.tone).toBe("loss");
    expect(VERDICT_STATUS.unknown.tone).toBe("unknown");
  });

  it("tallies a list of confidence values and labels it in fixed order, omitting empty states", () => {
    const counts = tallyConfidence(["verified", "verified", "disputed", "verified"]);
    expect(counts).toEqual({ verified: 3, corroborated: 0, unconfirmed: 0, disputed: 1 });
    expect(tallyConfidenceLabel(counts)).toBe("3 verified · 1 disputed");
    expect(tallyConfidence([])).toEqual({ verified: 0, corroborated: 0, unconfirmed: 0, disputed: 0 });
    expect(tallyConfidenceLabel(tallyConfidence([]))).toBe("");
  });
});

describe("formatting", () => {
  it("formats targets and observed values", () => {
    expect(formatTarget(">=", 5, "%")).toBe(">= 5 %");
    expect(formatTarget(">", 0.1 + 0.2, "USD bn")).toBe("> 0.3 USD bn");
    expect(formatObserved(9.628455, "%")).toBe("9.63%");
    expect(formatObserved(23434, "USD m")).toBe("23434.00 USD m");
    expect(formatObserved(null, "%")).toBe("—");
    expect(humanizeMetric("revenue_growth_yoy")).toBe("Revenue growth YoY");
    expect(humanizeMetric("eps_diluted")).toBe("EPS diluted");
    expect(metricLabel("concept:us-gaap:ResearchAndDevelopmentExpense")).toBe("Research and development expense");
    expect(metricLabel("concept:us-gaap:ConcentrationRiskPercentage1")).toBe("Concentration risk percentage");
    expect(metricLabel("concept:ifrs-full:EBITDAMargin")).toBe("EBITDA margin");
    expect(metricLabel("cost_of_revenue")).toBe("Cost of revenue");
  });
});

describe("fact values", () => {
  it("shows the printed number with its scale and currency", () => {
    expect(formatFactValue({ value_as_written: "94,036", scale: "millions", unit: "currency", currency: "USD" })).toBe("USD 94,036 m");
    expect(formatFactValue({ value_as_written: "1.57", scale: "units", unit: "per_share", currency: "USD" })).toBe("USD 1.57 / share");
    expect(formatFactValue({ value_as_written: "46.5", scale: "units", unit: "percent", currency: null })).toBe("46.5%");
    // Printed with its unit already: shown as printed, not decorated twice.
    expect(formatFactValue({ value_as_written: "€18.1 billion", scale: "billions", unit: "currency", currency: "EUR" })).toBe(
      "€18.1 billion",
    );
    expect(formatFactValue({ value_as_written: "14.1%", scale: "units", unit: "percent", currency: null })).toBe("14.1%");
    expect(formatFactValue({ value_as_written: "-1,204.5", scale: "millions", unit: "currency", currency: "USD" })).toBe(
      "USD -1,204.5 m",
    );
  });
});

describe("why this confidence", () => {
  it("combines the status reason with any failed check's detail, skipping a duplicate", () => {
    expect(
      confidenceReason({
        status_reason: "outside tolerance",
        checks: [
          { check: "sanity_bound", ok: false, detail: "outside tolerance" },
          { check: "unit_consistency", ok: false, detail: "unit mismatch" },
          { check: "restatement", ok: true, detail: "n/a" },
        ],
      }),
    ).toBe("outside tolerance — unit mismatch");
  });

  it("is null when there's nothing beyond the chip's own generic description", () => {
    expect(confidenceReason({ status_reason: null, checks: [] })).toBeNull();
    expect(confidenceReason({ status_reason: null, checks: [{ check: "sanity_bound", ok: true, detail: "" }] })).toBeNull();
  });

  it("names a concrete next step for a fact still awaiting review, and none once it's settled", () => {
    expect(confidenceNextAction("unverified")).toMatch(/review queue/);
    expect(confidenceNextAction("quarantined")).toMatch(/reconciliation/);
    expect(confidenceNextAction("verified")).toBeNull();
    expect(confidenceNextAction("rejected")).toBeNull();
  });
});

describe("asymmetric confidence weight", () => {
  const now = new Date("2026-06-01T00:00:00Z");

  it("quiets a Verified badge once it's held that long unchanged", () => {
    const justUnder = new Date(now.getTime() - (VERIFIED_QUIET_AFTER_DAYS - 1) * 86_400_000);
    const justOver = new Date(now.getTime() - (VERIFIED_QUIET_AFTER_DAYS + 1) * 86_400_000);
    expect(isLongStandingVerified("verified", justUnder, now)).toBe(false);
    expect(isLongStandingVerified("verified", justOver, now)).toBe(true);
  });

  it("never quiets a non-Verified confidence, however old", () => {
    const longAgo = new Date(now.getTime() - 365 * 86_400_000);
    expect(isLongStandingVerified("disputed", longAgo, now)).toBe(false);
    expect(isLongStandingVerified("unconfirmed", longAgo, now)).toBe(false);
  });

  it("never quiets without a since date", () => {
    expect(isLongStandingVerified("verified", null, now)).toBe(false);
    expect(isLongStandingVerified("verified", undefined, now)).toBe(false);
  });
});
