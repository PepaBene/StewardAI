import { describe, expect, it } from "vitest";

import {
  durationBetween,
  formatCost,
  formatDuration,
  isTerminal,
  jobHref,
  jobTypeLabel,
  modelsUsed,
  prettyJson,
  runError,
  totalCost,
} from "./job-detail";

const L = "en-US";

describe("job detail", () => {
  it("links and labels a job", () => {
    expect(jobHref(42)).toBe("/jobs/42");
    expect(jobTypeLabel("macro_collect")).toBe("Macro data");
    expect(jobTypeLabel("event_escalation")).toBe("Event escalation");
    expect(jobTypeLabel("something_new")).toBe("Something new");
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("running")).toBe(false);
  });

  it("formats durations", () => {
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(12_400)).toBe("12 s");
    expect(formatDuration(184_000)).toBe("3 min 4 s");
    expect(formatDuration(120_000)).toBe("2 min");
    expect(formatDuration(3_720_000)).toBe("1 h 2 min");
    expect(formatDuration(-1)).toBe("—");
    expect(durationBetween("2026-09-25T10:00:00Z", "2026-09-25T10:00:12Z")).toBe(12_000);
    expect(durationBetween("2026-09-25T10:00:00Z", null)).toBeNull();
  });

  it("sums the steps' cost, and says when there is none", () => {
    expect(totalCost([{ cost_usd: 0.012 }, { cost_usd: null }, { cost_usd: 0.0031 }])).toBeCloseTo(0.0151);
    expect(totalCost([{ cost_usd: null }])).toBeNull();
    expect(formatCost(0.0151, L)).toBe("$0.0151");
    expect(formatCost(2.5, L)).toBe("$2.50");
    expect(formatCost(null, L)).toBe("—");
  });

  it("lists the models once, and finds the error to show", () => {
    expect(modelsUsed([{ model: "gpt-6-luna" }, { model: null }, { model: "gpt-6-luna" }, { model: "gpt-6.1-sol" }])).toEqual([
      "gpt-6-luna",
      "gpt-6.1-sol",
    ]);
    const steps = [
      { step_index: 0, name: "fetch", status: "succeeded" as const, started_at: "", finished_at: null, error: null, detail: null, model: null, cost_usd: null },
      { step_index: 1, name: "classify", status: "failed" as const, started_at: "", finished_at: null, error: "rate limited", detail: null, model: null, cost_usd: null },
    ];
    expect(runError({ error: null, steps })).toEqual({ error: "rate limited", step: "classify" });
    expect(runError({ error: "boom", steps })).toEqual({ error: "boom", step: null });
    expect(runError({ error: null, steps: [steps[0]] })).toBeNull();
  });

  it("pretty-prints detail JSON, and nothing for an empty object", () => {
    expect(prettyJson({ items: 3 })).toBe('{\n  "items": 3\n}');
    expect(prettyJson({})).toBeNull();
    expect(prettyJson(null)).toBeNull();
  });
});
