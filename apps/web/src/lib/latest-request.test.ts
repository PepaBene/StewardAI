import { describe, expect, it } from "vitest";

import { latestOnly } from "./latest-request";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("latestOnly (load-more race)", () => {
  it("applies a page when no newer load started", async () => {
    const sequence = { current: 1 };
    expect(await latestOnly(sequence, async () => ["older"])).toEqual({ stale: false, ok: true, value: ["older"] });
  });

  it("drops an older-page answer when the filters changed while it loaded", async () => {
    const sequence = { current: 1 };
    const page = deferred<string[]>();
    const pending = latestOnly(sequence, () => page.promise);
    sequence.current += 1; // the filter change starts a fresh first-page load
    page.resolve(["page 2 of the old filters"]);
    expect(await pending).toEqual({ stale: true });
  });

  it("reports a failure as a message, unless it is stale too", async () => {
    const sequence = { current: 1 };
    expect(await latestOnly(sequence, () => Promise.reject(new Error("offline")))).toEqual({ stale: false, ok: false, error: "offline" });
    const page = deferred<string[]>();
    const pending = latestOnly(sequence, () => page.promise);
    sequence.current += 1;
    page.reject(new Error("offline"));
    expect(await pending).toEqual({ stale: true });
  });
});
