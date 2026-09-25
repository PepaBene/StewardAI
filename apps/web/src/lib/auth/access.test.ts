import { describe, expect, it } from "vitest";

import {
  decideAccess,
  isAuthFlowPath,
  isEmailAllowed,
  type AccessInput,
} from "./access";

const owner = { email: "owner@example.com" };

function decide(partial: Partial<AccessInput> & { pathname: string }) {
  return decideAccess({
    user: null,
    currentLevel: null,
    nextLevel: null,
    ...partial,
  });
}

describe("decideAccess: signed out", () => {
  it("allows /login", () => {
    expect(decide({ pathname: "/login" })).toEqual({ action: "allow" });
  });

  it.each(["/", "/holdings", "/status", "/config/sources", "/api/anything"])(
    "redirects %s to /login",
    (pathname) => {
      expect(decide({ pathname })).toEqual({ action: "redirect", to: "/login" });
    },
  );

  it.each(["/mfa/enroll", "/mfa/verify", "/mfa"])(
    "redirects MFA page %s to /login (MFA needs a first factor)",
    (pathname) => {
      expect(decide({ pathname })).toEqual({ action: "redirect", to: "/login" });
    },
  );
});

describe("decideAccess: aal1 session without a verified factor", () => {
  const base = { user: owner, currentLevel: "aal1", nextLevel: "aal1" } as const;

  it("allows /mfa/enroll", () => {
    expect(decide({ ...base, pathname: "/mfa/enroll" })).toEqual({ action: "allow" });
  });

  it.each(["/", "/holdings", "/status", "/login", "/mfa/verify"])(
    "redirects %s to /mfa/enroll",
    (pathname) => {
      expect(decide({ ...base, pathname })).toEqual({
        action: "redirect",
        to: "/mfa/enroll",
      });
    },
  );

  it("treats an unknown level like aal1 (fails closed)", () => {
    expect(
      decide({ user: owner, currentLevel: null, nextLevel: null, pathname: "/" }),
    ).toEqual({ action: "redirect", to: "/mfa/enroll" });
  });
});

describe("decideAccess: aal1 session with a verified factor (login without MFA)", () => {
  const base = { user: owner, currentLevel: "aal1", nextLevel: "aal2" } as const;

  it("allows /mfa/verify", () => {
    expect(decide({ ...base, pathname: "/mfa/verify" })).toEqual({ action: "allow" });
  });

  it.each(["/", "/holdings", "/reports", "/status", "/login", "/mfa/enroll"])(
    "rejects %s and redirects to /mfa/verify",
    (pathname) => {
      expect(decide({ ...base, pathname })).toEqual({
        action: "redirect",
        to: "/mfa/verify",
      });
    },
  );
});

describe("decideAccess: aal2 session", () => {
  const base = { user: owner, currentLevel: "aal2", nextLevel: "aal2" } as const;

  it.each(["/", "/holdings", "/activity", "/status", "/config/budget"])(
    "allows %s",
    (pathname) => {
      expect(decide({ ...base, pathname })).toEqual({ action: "allow" });
    },
  );

  it.each(["/login", "/mfa/enroll", "/mfa/verify", "/login/"])(
    "sends %s back home",
    (pathname) => {
      expect(decide({ ...base, pathname })).toEqual({ action: "redirect", to: "/" });
    },
  );
});

describe("decideAccess: single allowed user", () => {
  it("signs out a user whose email is not the allowed one", () => {
    expect(
      decide({
        user: { email: "intruder@example.com" },
        currentLevel: "aal2",
        nextLevel: "aal2",
        allowedEmail: "owner@example.com",
        pathname: "/",
      }),
    ).toEqual({ action: "redirect", to: "/login?error=not_allowed", signOut: true });
  });

  it("matches the allowed email case-insensitively", () => {
    expect(
      decide({
        user: { email: "Owner@Example.com" },
        currentLevel: "aal2",
        nextLevel: "aal2",
        allowedEmail: " owner@example.com ",
        pathname: "/",
      }),
    ).toEqual({ action: "allow" });
  });

  it("ignores the check when no allowed email is configured", () => {
    expect(isEmailAllowed("anyone@example.com", null)).toBe(true);
    expect(isEmailAllowed(null, "owner@example.com")).toBe(false);
  });
});

describe("isAuthFlowPath", () => {
  it.each([
    ["/login", true],
    ["/mfa/enroll", true],
    ["/mfa/verify", true],
    ["/", false],
    ["/loginx", false],
    ["/mfax", false],
    ["/holdings", false],
  ])("%s -> %s", (path, expected) => {
    expect(isAuthFlowPath(path)).toBe(expected);
  });
});
