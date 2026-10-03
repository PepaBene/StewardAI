import { describe, expect, it } from "vitest";

import { isInterruptedError, splitReasonUrl, stepFindings } from "./step-findings";

describe("splitReasonUrl", () => {
  it("separates the named cause from the URL the backend appends", () => {
    expect(splitReasonUrl("bad xml [https://www.sec.gov/a.xml]")).toEqual({ cause: "bad xml", url: "https://www.sec.gov/a.xml" });
    expect(splitReasonUrl("no url here")).toEqual({ cause: "no url here", url: null });
    expect(splitReasonUrl("javascript [javascript:alert(1)]")).toEqual({ cause: "javascript [javascript:alert(1)]", url: null });
  });
});

describe("stepFindings", () => {
  it("is null for no detail, an empty detail, or fields of the wrong shape", () => {
    expect(stepFindings(null)).toBeNull();
    expect(stepFindings({})).toBeNull();
    expect(stepFindings({ deferred_by_issuer: "x", parse_failures: "x", parse_failed: "3" })).toBeNull();
  });
  it("reads deferred counts and ignores malformed ones (unknown is not zero)", () => {
    const f = stepFindings({ deferred_by_issuer: { "1": 5, "2": "many", "3": -1 } });
    expect(f?.deferred).toEqual([{ cik: "1", count: 5 }]);
    expect(f?.deferredTotal).toBe(5);
  });
  it("keeps a failure with no recorded reason visible, saying so", () => {
    const f = stepFindings({ parse_failures: [{ accession: "A-1", form: "SC 13D", reason: "" }] });
    expect(f?.parseFailures).toEqual([{ accession: "A-1", form: "SC 13D", cause: "no reason was recorded", url: null }]);
  });
});

describe("isInterruptedError", () => {
  it("recognises the backend's interrupted wording only", () => {
    expect(isInterruptedError("interrupted (the process that started it stopped; run 9 began)")).toBe(true);
    expect(isInterruptedError("interrupted: the worker stopped during this step")).toBe(true);
    expect(isInterruptedError("ValueError: the call was interrupted")).toBe(false);
    expect(isInterruptedError(null)).toBe(false);
  });
});
