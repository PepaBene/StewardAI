import { describe, expect, it } from "vitest";

import { ageWords, plainProviderProblem, sentenceCase, subjectWords } from "./plain";

describe("subjectWords", () => {
  it("turns a provider key into words and leaves plain names alone", () => {
    expect(subjectWords("alpha_vantage/account_free")).toBe("Alpha vantage, account free");
    expect(subjectWords("Apple Inc.")).toBe("Apple Inc.");
    expect(sentenceCase("no thesis is written.")).toBe("No thesis is written");
  });
});

describe("plainProviderProblem", () => {
  it("explains a refused key and keeps the raw status", () => {
    const p = plainProviderProblem({ statusCode: 403, state: "denied", detail: "forbidden" });
    expect(p.sentence).toMatch(/refused/);
    expect(p.raw).toContain("HTTP 403");
  });
  it("explains a spent allowance", () => {
    expect(plainProviderProblem({ statusCode: 429 }).sentence).toMatch(/allowance/);
  });
  it("falls back to a neutral sentence", () => {
    expect(plainProviderProblem({}).raw).toBe("No details recorded.");
  });
});

describe("ageWords", () => {
  const now = new Date("2026-10-16T12:00:00Z");
  it("reads recent and old timestamps", () => {
    expect(ageWords("2026-10-16T11:30:00Z", now)).toBe("30 min ago");
    expect(ageWords("2026-10-13T12:00:00Z", now)).toBe("3 days ago");
    expect(ageWords(null, now)).toBe("never");
  });
});
