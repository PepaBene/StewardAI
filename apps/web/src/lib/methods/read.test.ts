import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { parseIndex } from "./markdown";
import { methodSlugs, readMethod } from "./read";

describe("method documents", () => {
  it("finds documents and every slug resolves to a non-empty file", () => {
    const slugs = methodSlugs();
    expect(slugs.length).toBeGreaterThan(0);
    for (const slug of slugs) {
      const source = readMethod(slug);
      expect(source, slug).not.toBeNull();
      expect(source!.trim().length, slug).toBeGreaterThan(0);
    }
  });

  it("includes the documents the app links to", () => {
    expect(methodSlugs()).toContain("dividend_income");
  });

  it("resolves every slug listed in the index", () => {
    const index = readMethod("index");
    expect(index).not.toBeNull();
    for (const entry of parseIndex(index!)) {
      expect(readMethod(entry.slug), entry.slug).not.toBeNull();
    }
  });

  it("rejects unsafe slugs", () => {
    expect(readMethod("../package")).toBeNull();
    expect(readMethod("nope_does_not_exist")).toBeNull();
  });

  it("keeps the committed copy in step with docs/methods", () => {
    const source = path.join(process.cwd(), "..", "..", "docs", "methods");
    const copy = path.join(process.cwd(), "content", "methods");
    if (!existsSync(source)) return;
    for (const f of readdirSync(source).filter((n) => n.endsWith(".md"))) {
      expect(readFileSync(path.join(copy, f), "utf-8"), `${f}: run node scripts/sync-methods.mjs`).toBe(
        readFileSync(path.join(source, f), "utf-8"),
      );
    }
  });
});
