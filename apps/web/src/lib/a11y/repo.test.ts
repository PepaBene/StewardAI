import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

import { countByRule, scanSource } from "./scan";

/**
 * A ratchet: files may carry the unnamed tables, unscoped headers and unnamed svgs recorded in
 * `baseline.json`, and no file may get worse. Fixing one lowers the number; run with
 * `A11Y_WRITE_BASELINE=1` to record the lower figure. A new file starts at zero.
 */
const ROOT = join(__dirname, "..", "..");
const BASELINE = join(__dirname, "baseline.json");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return path.endsWith(".tsx") ? [path] : [];
  });
}

function current() {
  const out: Record<string, Record<string, number>> = {};
  for (const file of sources(ROOT)) {
    const counts = countByRule(scanSource(readFileSync(file, "utf8")));
    const worst = Object.fromEntries(Object.entries(counts).filter(([, n]) => n > 0));
    if (Object.keys(worst).length > 0) out[relative(ROOT, file).split(sep).join("/")] = worst;
  }
  return out;
}

describe("accessibility ratchet", () => {
  const found = current();
  if (process.env.A11Y_WRITE_BASELINE === "1") {
    writeFileSync(BASELINE, `${JSON.stringify(found, null, 2)}\n`);
  }
  const baseline = JSON.parse(readFileSync(BASELINE, "utf8")) as typeof found;

  it("adds no unnamed table, unscoped header or unnamed svg", () => {
    const worse: string[] = [];
    for (const [file, counts] of Object.entries(found)) {
      for (const [rule, n] of Object.entries(counts)) {
        const allowed = baseline[file]?.[rule] ?? 0;
        if (n > allowed) worse.push(`${file}: ${rule} ${n} (allowed ${allowed})`);
      }
    }
    expect(worse).toEqual([]);
  });

  it("keeps the baseline honest: a fixed file lowers its number", () => {
    const stale: string[] = [];
    for (const [file, counts] of Object.entries(baseline)) {
      for (const [rule, n] of Object.entries(counts)) {
        const now = found[file]?.[rule] ?? 0;
        if (now < n) stale.push(`${file}: ${rule} now ${now}, baseline ${n}`);
      }
    }
    expect(stale).toEqual([]);
  });

  it("covers the release pages with no exception", () => {
    const release = Object.keys(found).filter((f) => f.includes("release"));
    expect(release).toEqual([]);
  });
});
