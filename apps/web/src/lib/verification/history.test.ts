import { describe, expect, it } from "vitest";

import { parseHistory } from "./history";

const audit = (at: string, sample: number, correct: number, precision: number | null) => ({
  id: 1,
  audited_at: at,
  sample_size: sample,
  evidence_correct: correct,
  evidence_precision: precision,
  pit_checked: 5,
  pit_violations: 0,
  reviewer: "owner",
});

describe("parseHistory", () => {
  it("orders audits oldest first and reads the trend", () => {
    const h = parseHistory({ audits: [audit("2026-03-01T00:00:00Z", 50, 49, 0.98), audit("2026-01-01T00:00:00Z", 50, 45, 0.9)] });
    expect(h.audits.map((a) => a.precision)).toEqual([0.9, 0.98]);
    expect(h.trend).toBe("up");
    expect(h.latest?.at).toContain("2026-03");
  });

  it("keeps an empty sample as unknown precision, never zero", () => {
    const h = parseHistory({ audits: [audit("2026-01-01T00:00:00Z", 0, 0, null)] });
    expect(h.audits[0].precision).toBeNull();
    expect(h.trend).toBe("single");
  });

  it("returns no audits for a malformed payload", () => {
    expect(parseHistory(null)).toEqual({ audits: [], latest: null, trend: "none" });
  });
});
