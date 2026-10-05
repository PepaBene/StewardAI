import { describe, expect, it } from "vitest";

import { apiErrorText, utcFromLocalInput } from "./form-utils";
import { emptyForwardDraft, emptySessionDraft, forwardRequest, parseSessions, sessionRequest, sessionWindowError, validateForward, validateSession } from "./forms";

const goodSession = { ...emptySessionDraft(), mic: "xnys", date: "2026-11-27", state: "half_day" as const, openAt: "2026-11-27T14:30", closeAt: "2026-11-27T18:00", source: "NYSE", evidenceUrl: "https://nyse.com/holidays" };

describe("exchange session form", () => {
  it("requires times unless closed and close after open", () => {
    expect(validateSession(goodSession)).toEqual({});
    expect(validateSession({ ...goodSession, openAt: "" }).openAt).toBeDefined();
    expect(validateSession({ ...goodSession, closeAt: "2026-11-27T14:00" }).closeAt).toBeDefined();
    expect(validateSession({ ...goodSession, state: "closed", openAt: "", closeAt: "" })).toEqual({});
  });
  it("sends UTC times, drops them when closed, uppercases the MIC", () => {
    expect(sessionRequest(goodSession)).toMatchObject({ mic: "XNYS", open_at: "2026-11-27T14:30:00Z", reason: null });
    expect(sessionRequest({ ...goodSession, state: "closed" })).toMatchObject({ open_at: null, close_at: null });
  });
  it("sorts recorded rows with the confirmed one first and keeps unknown states", () => {
    const row = { mic: "XNYS", source: "owner", known_at: "2026-01-01" };
    const rows = parseSessions([
      { ...row, session_date: "2026-01-02", state: "closed", confirmed: false },
      { ...row, session_date: "2026-01-02", state: "half_day", confirmed: true },
      { ...row, session_date: "2026-01-01", state: "odd", confirmed: false },
    ]);
    expect(rows.map((r) => r.state)).toEqual(["unknown", "half_day", "closed"]);
  });
  it("checks the window", () => {
    expect(sessionWindowError("2026-02-01", "2026-01-01")).not.toBeNull();
    expect(sessionWindowError("2026-01-01", "2026-02-01")).toBeNull();
  });
});

const goodFwd = { ...emptyForwardDraft(), metric: "Revenue", fiscalPeriod: "FY2027", provider: "ACME 10-K", low: "10", high: "12", sourceUrl: "https://acme.com/ir" };

describe("forward observation form", () => {
  it("needs a value or range with low <= high", () => {
    expect(validateForward(goodFwd)).toEqual({});
    expect(validateForward({ ...goodFwd, low: "", high: "" }).value).toBeDefined();
    expect(validateForward({ ...goodFwd, low: "13" }).low).toBeDefined();
  });
  it("keeps scenarios off vendors and guidance attributed", () => {
    expect(validateForward({ ...goodFwd, kind: "owner_scenario", provider: "Bloomberg" }).provider).toBeDefined();
    expect(validateForward({ ...goodFwd, provider: "" }).provider).toBeDefined();
  });
  it("maps blanks to null and never to 0", () => {
    const r = forwardRequest(goodFwd);
    expect(r).toMatchObject({ kind: "company_guidance", metric: "revenue", value: null, low: 10, high: 12, currency: null });
    expect(forwardRequest({ ...goodFwd, kind: "owner_scenario" }).provider).toBe("owner");
  });
});

describe("form utils", () => {
  it("reads datetime-local as UTC", () => {
    expect(utcFromLocalInput("2026-01-01T09:30")).toBe("2026-01-01T09:30:00Z");
    expect(utcFromLocalInput("")).toBeNull();
  });
  it("formats validation errors", () => {
    expect(apiErrorText({ detail: [{ loc: ["body", "mic"], msg: "bad" }] }, 422)).toContain("mic: bad");
    expect(apiErrorText(undefined, 500)).toBe("Not saved (HTTP 500).");
  });
});
