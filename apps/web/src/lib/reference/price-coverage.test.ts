import { describe, expect, it } from "vitest";

import {
  basisLabel,
  parseCoverage,
  parseExpected,
  parseSessions,
  sessionCounts,
  stripLayout,
  validateCoverage,
} from "./price-coverage";

describe("session strip", () => {
  const raw = [
    { session: "2026-09-08", state: "observed" },
    { session: "2026-09-07", state: "missing" },
    { session: "2026-09-04", state: "disagrees" },
    { session: "bad", state: "observed" },
    { session: "2026-09-09", state: "weird" },
  ];

  it("sorts oldest first, drops bad dates and never promotes unknown states", () => {
    const cells = parseSessions(raw);
    expect(cells.map((c) => c.session)).toEqual(["2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09"]);
    expect(cells[3].state).toBe("missing");
  });

  it("counts states", () => {
    expect(sessionCounts(parseSessions(raw))).toEqual({ total: 4, observed: 1, missing: 2, disagrees: 1 });
  });

  it("aligns weeks into columns and weekdays into rows", () => {
    const l = stripLayout(parseSessions(raw));
    expect(l.columns).toBe(2);
    expect(l.rows).toBe(5);
    const fri = l.cells[0];
    expect([fri.column, fri.row]).toEqual([1, 5]);
    const mon = l.cells[1];
    expect([mon.column, mon.row]).toEqual([2, 1]);
  });

  it("grows to seven rows when a weekend session exists", () => {
    expect(stripLayout([{ session: "2026-09-05", state: "observed" }]).rows).toBe(7);
  });

  it("handles empty input", () => {
    expect(parseSessions(null)).toEqual([]);
    expect(stripLayout([]).columns).toBe(0);
  });
});

describe("exchange sessions", () => {
  it("labels the basis", () => {
    expect(basisLabel("confirmed_sessions")).toBe("Confirmed sessions");
    expect(basisLabel("weekdays_less_configured_closures")).toBe("Weekdays less configured closures");
    expect(basisLabel(null)).toBe("Calendar basis not stated");
  });

  it("reads the session count and the number of confirmed days, and invents no special days", () => {
    const info = parseExpected({
      mic: "XNYS", start: "2026-08-01", end: "2026-09-30", basis: "confirmed_sessions", count: 20, confirmed_days: 2, days: [],
    });
    expect(info.count).toBe(20);
    expect(info.confirmedDays).toBe(2);
    expect(info.special).toEqual([]);
  });
});

describe("corporate-action coverage", () => {
  const entry = { provider: "x", checked_at: "2026-09-01", owner_verified: false };
  it("never calls a missing or unknown state covered", () => {
    expect(parseCoverage({ symbol: "X", entries: [] })).toEqual([]);
    expect(parseCoverage(null)).toEqual([]);
    const [e] = parseCoverage({ symbol: "X", entries: [{ ...entry, state: "no_data" }] });
    expect(e.covered).toBe(false);
    expect(e.stateLabel).toBe("no data");
    expect(parseCoverage({ symbol: "X", entries: [{ ...entry, state: "not_covered" }] })[0].covered).toBe(false);
    expect(parseCoverage({ symbol: "X", entries: [{ ...entry, state: "available" }] })[0].covered).toBe(true);
  });

  it("lists the owner-verified entry first", () => {
    const rows = parseCoverage({
      symbol: "X",
      entries: [
        { provider: "a", state: "available", checked_at: "2026-09-02", owner_verified: false },
        { provider: "owner", state: "available", checked_at: "2026-08-01", owner_verified: true },
      ],
    });
    expect(rows[0].provider).toBe("owner");
    expect(rows[0].ownerVerified).toBe(true);
  });

  const ok = { through: "2026-09-01", sourceUrl: "https://example.com", reason: "Checked the issuer page" };
  it("accepts a valid draft", () => {
    expect(validateCoverage(ok, "2026-09-30")).toEqual({});
  });
  it("enforces the API limits and date rules", () => {
    expect(validateCoverage({ ...ok, sourceUrl: "short" }, "2026-09-30").sourceUrl).toBeDefined();
    expect(validateCoverage({ ...ok, reason: "too short" }, "2026-09-30").reason).toBeDefined();
    expect(validateCoverage({ ...ok, through: "" }, "2026-09-30").through).toBeDefined();
    expect(validateCoverage({ ...ok, through: "2026-13-40" }, "2026-09-30").through).toBeDefined();
    expect(validateCoverage({ ...ok, through: "2026-10-05" }, "2026-09-30").through).toMatch(/future/);
  });
});
