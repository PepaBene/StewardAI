import { describe, expect, it } from "vitest";

import { snoozeUntil } from "./journal/journal";
import { auditState, type FeatureReadiness } from "./release/release";
import { pathLabel, type EventPathData } from "./xray/owner-input";

describe("snoozeUntil", () => {
  it("adds days across a month end in local time", () => {
    expect(snoozeUntil(7, new Date(2026, 9, 28))).toBe("2026-11-04");
    expect(snoozeUntil(30, new Date(2026, 11, 20))).toBe("2027-01-19");
  });
});

describe("auditState", () => {
  const base = { audit: "thin", audited_facts: 2 } as unknown as FeatureReadiness;
  it("says what a thin audit still needs and does not pass", () => {
    const s = auditState(base);
    expect(s.passed).toBe(false);
    expect(s.label).toContain("at least 3 facts on 2 subjects");
  });
});

describe("pathLabel", () => {
  const edge = (supplier: string, customer: string) => ({ supplier, customer }) as EventPathData["edges"][number];
  it("reads a downstream path from the disrupted supplier to its customers", () => {
    const p = { start: "A", end: "C", edges: [edge("A", "B"), edge("B", "C")] } as EventPathData;
    expect(pathLabel(p)).toBe("A → B → C");
  });
  it("reads an upstream path from the lost customer to its suppliers", () => {
    const p = { start: "C", end: "A", edges: [edge("B", "C"), edge("A", "B")] } as EventPathData;
    expect(pathLabel(p)).toBe("C → B → A");
  });
});
