import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

describe("network view sibling keys", () => {
  it("never gives two sibling elements the same React key", () => {
    // Siblings that reset on a ticker change were all keyed `key={ticker}`, which React warns
    // about (duplicate keys omit or duplicate children). Each needs its own prefix.
    const source = readFileSync(fileURLToPath(new URL("./network-view.tsx", import.meta.url)), "utf8");
    expect(source.match(/key=\{ticker\}/g) ?? []).toHaveLength(0);
    const keys = [...source.matchAll(/key=\{`([a-z-]+)-\$\{ticker\}`\}/g)].map((m) => m[1]);
    expect(keys.length).toBeGreaterThan(0);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
