import { describe, expect, it } from "vitest";

import type { VerificationConfig } from "@/lib/api/client";

import {
  DEFAULT_VERIFICATION,
  newOverride,
  overrideErrorPath,
  validateVerificationForm,
  verificationFromBody,
  verificationToBody,
} from "./verification-form";

/** Every field set away from its default, including nulls and a field this form does not know. */
const FULL = {
  schema_version: 1,
  sanity_max_change_qoq: 0.35,
  sanity_overrides: { operating_income: 1.25, net_income: null, revenue: 0.075 },
  stated_ratio_tolerance_pp: 0.3,
  period_end_tolerance_days: 14,
  max_report_lag_days: 120,
  corroboration: { tier1_confirms_alone: false, independent_sources_to_confirm: 3 },
  trust: { wrong_penalty: 0.25, useful_reward: 0.1, demote_below: 0.5 },
  claim_verifier: { enabled: false, model: "gpt-6.1-sol", effort: "low", mode: "mark", numeric_check: false, date_check: true, quote_check: true, applies_to: ["weekly_digest", "escalation"] },
  future_field: { kept: true },
} as unknown as VerificationConfig;

describe("verification form", () => {
  it("round-trips a fully populated body without loss", () => {
    const form = verificationFromBody(FULL);
    expect(form.sanityMaxChange).toBe("35");
    expect(form.overrides.map((o) => [o.metric, o.disabled, o.band])).toEqual([
      ["operating_income", false, "125"],
      ["net_income", true, ""],
      ["revenue", false, "7.5"],
    ]);
    expect(validateVerificationForm(form)).toEqual({});
    expect(verificationToBody(form)).toEqual(FULL);
  });

  it("round-trips the defaults, and starts from them when nothing is saved", () => {
    expect(verificationToBody(verificationFromBody(DEFAULT_VERIFICATION))).toEqual(DEFAULT_VERIFICATION);
    expect(verificationToBody(verificationFromBody(null))).toEqual(DEFAULT_VERIFICATION);
  });

  it("disables the default sanity band with null, keeping the typed value for later", () => {
    const form = { ...verificationFromBody(null), sanityEnabled: false };
    expect(verificationToBody(form).sanity_max_change_qoq).toBeNull();
    const back = verificationFromBody({ ...DEFAULT_VERIFICATION, sanity_max_change_qoq: null });
    expect(back).toMatchObject({ sanityEnabled: false, sanityMaxChange: "50" });
  });

  it("drops an empty new override row", () => {
    const form = { ...verificationFromBody(null), overrides: [newOverride()] };
    expect(verificationToBody(form).sanity_overrides).toEqual({});
    expect(validateVerificationForm(form)).toEqual({});
  });

  it("flags every out-of-range field at its body path before saving", () => {
    const form = {
      ...verificationFromBody(null),
      sanityMaxChange: "0",
      overrides: [newOverride("Revenue", "10"), newOverride("ebit", "abc"), newOverride("ebit", "5")],
      statedRatioTolerancePp: "-1",
      periodEndToleranceDays: "40",
      maxReportLagDays: "12.5",
      independentSources: "1",
      wrongPenalty: "1.5",
      usefulReward: "",
      demoteBelow: "x",
      claimModel: "gpt-5",
    };
    expect(validateVerificationForm(form)).toEqual({
      sanity_max_change_qoq: ["Must be more than 0."],
      "sanity_overrides.Revenue": [expect.stringMatching(/^A metric id/)],
      "sanity_overrides.ebit": ["ebit is listed twice."],
      stated_ratio_tolerance_pp: ["At least 0."],
      period_end_tolerance_days: ["At most 31."],
      max_report_lag_days: ["Enter a whole number."],
      "corroboration.independent_sources_to_confirm": ["At least 2."],
      "trust.wrong_penalty": ["At most 1."],
      "trust.useful_reward": ["Required."],
      "trust.demote_below": ["Enter a number."],
      "claim_verifier.model": [expect.stringMatching(/gpt- or gemini- model/)],
    });
    // A bad band on a unique metric is reported on that metric's row.
    const one = { ...verificationFromBody(null), overrides: [newOverride("ebit", "abc")] };
    expect(validateVerificationForm(one)).toEqual({ "sanity_overrides.ebit": ["Enter a number."] });
    expect(overrideErrorPath({ metric: " ebit " })).toBe("sanity_overrides.ebit");
  });

  it("sends a value it cannot read as typed, for the backend to reject", () => {
    const form = { ...verificationFromBody(null), maxReportLagDays: "soon" };
    expect(verificationToBody(form).max_report_lag_days).toBe("soon");
  });
});

describe("verification form: claim verifier effort", () => {
  it("sends an unset effort as null and accepts one on any model", () => {
    const form = verificationFromBody(null);
    expect(form.claimEffort).toBe("");
    expect(verificationToBody(form).claim_verifier!.effort).toBeNull();
    form.claimEffort = "low";
    expect(validateVerificationForm(form)["claim_verifier.effort"]).toBeUndefined();
    form.claimModel = "gpt-6.1-sol";
    expect(validateVerificationForm(form)["claim_verifier.effort"]).toBeUndefined();
    expect(verificationToBody(form).claim_verifier!.effort).toBe("low");
  });
});
