import { describe, expect, it } from "vitest";

import { formatCost, formatMs, outcomeLabel, traceOf, traceTotals, validationLine } from "./trace";

describe("traceOf", () => {
  it("is null for an older or purged report", () => {
    expect(traceOf({})).toBeNull();
    expect(traceOf({ trace: null })).toBeNull();
    expect(traceOf(undefined)).toBeNull();
  });

  it("fills missing lists so the panel never reads undefined", () => {
    const t = traceOf({ trace: { model_calls: [{ step: "plan", model: "m" }] } });
    expect(t?.web).toEqual([]);
    expect(t?.validation).toEqual([]);
    expect(t?.model_calls).toHaveLength(1);
  });
});

describe("formatting", () => {
  it("shows small costs with four digits and says when a model has no price", () => {
    expect(formatCost(0.0042)).toBe("$0.0042");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(formatCost(null)).toBe("no price");
  });

  it("formats times", () => {
    expect(formatMs(0)).toBe("0 ms");
    expect(formatMs(850)).toBe("850 ms");
    expect(formatMs(2500)).toBe("2.5 s");
  });

  it("names outcomes in words", () => {
    expect(outcomeLabel("schema_failure")).toBe("failed the schema");
    expect(outcomeLabel(undefined)).toBe("ok");
  });
});

describe("traceTotals and validationLine", () => {
  it("counts failures, schema failures and dropped sentences", () => {
    const trace = traceOf({
      trace: {
        model_calls: [
          { step: "a", model: "m", input_tokens: 10, output_tokens: 5, outcome: "ok" },
          { step: "b", model: "m", outcome: "schema_failure" },
        ],
        validation: [
          { step: "b", schema_failure: true },
          { step: "a", dropped_uncited: 2, dropped_by_check: 1, links_removed: 1 },
        ],
        total_cost_usd: 0.25,
        total_ms: 1500,
      },
    });
    const lines = traceTotals(trace!);
    expect(lines[0]).toContain("2 model calls");
    expect(lines.join(" ")).toContain("1 call did not produce a section");
    expect(lines.join(" ")).toContain("1 answer failed the output schema");
    expect(lines.join(" ")).toContain("3 sentences dropped");
    expect(validationLine({ step: "a", dropped_uncited: 2, dropped_by_check: 1, links_removed: 1 })).toBe(
      "2 without a checkable source, 1 removed by the claim check, 1 link removed",
    );
    expect(validationLine({ step: "a" })).toBe("");
  });
});
