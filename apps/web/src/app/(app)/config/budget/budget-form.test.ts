import { describe, expect, it } from "vitest";

import { budgetFromBody, budgetToBody, currentBudgetMonth, DEFAULT_BUDGET, newJobCapRow, validateBudgetForm } from "./budget-form";

describe("budget form", () => {
  it("round-trips a saved body, keeping fields it does not know", () => {
    const body = {
      ...DEFAULT_BUDGET,
      monthly_cap_usd: 50,
      warn_at_fraction: 0.75,
      at_cap: "downgrade_noncritical" as const,
      per_job_cap_usd: { research: 2.5 },
      future_field: true,
    };
    const form = budgetFromBody(body);
    expect(form.warnPercent).toBe("75");
    expect(budgetToBody(form)).toEqual(body);
    expect(validateBudgetForm(form)).toEqual({});
  });

  it("an old body without limits gets the defaults: no caps, pause non-critical jobs", () => {
    const form = budgetFromBody({ schema_version: 1, web_search_per_1000: 10 });
    const body = budgetToBody(form);
    expect(body.monthly_cap_usd).toBeNull();
    expect(body.default_job_cap_usd).toBeNull();
    expect(body.at_cap).toBe("pause_noncritical");
    expect(body.critical_job_types).toEqual(["earnings_check", "event_escalation", "chat"]);
  });

  it("flags bad caps, duplicate rows and an unpriced downgrade model before saving", () => {
    const form = budgetFromBody(null);
    form.monthlyCap = "0";
    form.warnPercent = "120";
    form.atCap = "downgrade_noncritical";
    form.downgradeModel = "claude-unpriced-1";
    form.prices = [...form.prices, { ...form.prices[0], key: "dup" }];
    form.jobCaps = [
      { ...newJobCapRow(), jobType: "research", cap: "1" },
      { ...newJobCapRow(), jobType: "research", cap: "-1" },
      newJobCapRow(),
    ];
    const errors = validateBudgetForm(form);
    expect(Object.keys(errors).sort()).toEqual(
      [
        "monthly_cap_usd",
        "warn_at_fraction",
        "downgrade_model",
        `prices.${form.prices.length - 1}.model`,
        "per_job_cap_usd.row1",
        "per_job_cap_usd.row2",
      ].sort(),
    );
  });

  it("counts months in Prague time", () => {
    expect(currentBudgetMonth(new Date("2026-09-30T22:30:00Z"))).toBe("2026-10");
    expect(currentBudgetMonth(new Date("2026-09-30T21:30:00Z"))).toBe("2026-09");
  });
});
