import { describe, expect, it } from "vitest";

import { COMPLIANCE_LABELS, complianceLine, complianceOf } from "./ledger";

describe("rule compliance in the ledger", () => {
  it("is null for a thesis KPI and for a report stored before the check existed", () => {
    expect(complianceOf({ kind: "thesis_kpi" })).toBeNull();
    expect(complianceOf(undefined)).toBeNull();
  });

  it("states the measured value, the limit and the date", () => {
    const c = complianceOf({
      compliance: { status: "warn", measured: 0.0838, limit: 0.09, operator: "<=", as_of: "2026-10-10", reason: "holds" },
    })!;
    expect(complianceLine(c)).toBe("Measured 8.38 % against <= 9.00 % as of 2026-10-10");
    expect(COMPLIANCE_LABELS[c.status]).toContain("close to the limit");
  });

  it("shows counts as whole numbers and marks portfolio-wide rules", () => {
    const line = complianceLine({ status: "pass", measured: 12, limit: 20, operator: "<=", unit: "count", scope: "portfolio", reason: "holds" });
    expect(line).toBe("Measured 12 against <= 20 (whole portfolio)");
  });

  it("gives the reason when a rule was not checked", () => {
    expect(complianceLine({ status: "unknown", reason: "not held" })).toBe("not held");
  });
});
