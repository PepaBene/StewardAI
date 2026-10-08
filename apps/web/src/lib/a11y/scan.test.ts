import { describe, expect, it } from "vitest";

import { countByRule, scanSource } from "./scan";

describe("scanSource", () => {
  it("accepts a captioned table with scoped headers", () => {
    const src = `<table><caption className="sr-only">Holdings</caption><thead><tr><th scope="col">A</th></tr></thead></table>`;
    expect(scanSource(src)).toEqual([]);
  });

  it("accepts a table named by aria-label or aria-labelledby, also across lines", () => {
    expect(scanSource(`<table\n  aria-label="Holdings"\n>x</table>`)).toEqual([]);
    expect(scanSource(`<table aria-labelledby="h">x</table>`)).toEqual([]);
  });

  it("flags an unnamed table and an unscoped header", () => {
    const found = countByRule(scanSource(`<table><thead><tr><th>A</th></tr></thead></table>`));
    expect(found).toEqual({ "table-name": 1, "th-scope": 1, "svg-name": 0 });
  });

  it("flags an svg that is neither hidden nor named", () => {
    expect(countByRule(scanSource(`<svg viewBox="0 0 1 1"></svg>`))["svg-name"]).toBe(1);
    expect(scanSource(`<svg aria-hidden="true"></svg><svg role="img" aria-label="Trend"></svg>`)).toEqual([]);
  });

  it("does not take a thead or th-like tag for a header cell", () => {
    expect(scanSource(`<table aria-label="x"><thead><tr><td>1</td></tr></thead></table>`)).toEqual([]);
  });
});
