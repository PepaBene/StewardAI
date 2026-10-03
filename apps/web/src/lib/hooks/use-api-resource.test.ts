import { describe, expect, it } from "vitest";

import { mergeSettled, type ResourceState } from "./use-api-resource";

const ok = (data: number): ResourceState<number> => ({ kind: "ok", data });
const err = (previous: number | null): ResourceState<number> => ({ kind: "error", title: "t", detail: "d", previous });

describe("mergeSettled", () => {
  it("applies a success over anything", () => {
    expect(mergeSettled(err(1), ok(2))).toEqual(ok(2));
    expect(mergeSettled({ kind: "loading" }, ok(2))).toEqual(ok(2));
  });

  it("keeps the last good data beside a failure", () => {
    expect(mergeSettled(ok(1), err(null))).toMatchObject({ kind: "error", previous: 1 });
  });

  it("keeps the last good data across a second failure in a row", () => {
    expect(mergeSettled(err(1), err(null))).toMatchObject({ kind: "error", previous: 1 });
  });

  it("has no previous data when the first request fails", () => {
    expect(mergeSettled({ kind: "loading" }, err(null))).toMatchObject({ kind: "error", previous: null });
  });
});
