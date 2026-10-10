import { describe, expect, it } from "vitest";

import { buildAudit, EMPTY_AUDIT, pilotDayLabel, progressLabel, progressShare, type AuditForm, type TargetProgress } from "./release";

const filled: AuditForm = {
  ...EMPTY_AUDIT,
  subject: "SAP",
  region: "Europe",
  field: "Revenue 2025",
  displayed: "34.2 bn EUR",
  source_value: "34,176 million EUR",
  source_url: "https://example.com/sap-annual-report",
};

describe("buildAudit", () => {
  it("accepts a complete comparison and trims it", () => {
    const built = buildAudit({ ...filled, subject: " SAP " });
    expect(built.ok).toBe(true);
    if (built.ok) expect(built.body.subject).toBe("SAP");
  });

  it("asks for the source value and link when the fact was compared", () => {
    expect(buildAudit({ ...filled, source_value: "" }).ok).toBe(false);
    expect(buildAudit({ ...filled, source_url: "not a link" }).ok).toBe(false);
    expect(buildAudit({ ...filled, source_url: "ftp://example.com/x" }).ok).toBe(false);
  });

  it("does not ask for a source when the fact could not be checked", () => {
    const built = buildAudit({ ...filled, result: "not_checkable", source_value: "", source_url: "" });
    expect(built.ok).toBe(true);
    if (built.ok) expect(built.body.source_url).toBeNull();
  });

  it("asks for a region only for securities", () => {
    expect(buildAudit({ ...filled, region: "" }).ok).toBe(false);
    expect(buildAudit({ ...filled, subject_kind: "etf", region: "" }).ok).toBe(true);
  });

  it("names the missing piece", () => {
    const built = buildAudit({ ...filled, field: " " });
    expect(built.ok).toBe(false);
    if (!built.ok) expect(built.error).toMatch(/which fact/i);
  });
});

describe("progress", () => {
  const p: TargetProgress = { subject_kind: "security", target: 20, audited: 14, clean: 12, with_mismatch: 2 };
  it("shows clean over target", () => expect(progressLabel(p)).toBe("12 of 20"));
  it("caps the bar at full", () => {
    expect(progressShare(p)).toBeCloseTo(0.6);
    expect(progressShare({ ...p, clean: 25 })).toBe(1);
  });
});

describe("pilotDayLabel", () => {
  it("counts the day", () => expect(pilotDayLabel(12, 30, false)).toBe("Day 12 of 30"));
  it("closes the pilot", () => expect(pilotDayLabel(31, 30, true)).toBe("Finished after 30 days"));
});
