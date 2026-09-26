import { describe, expect, it } from "vitest";

import type { ConfigVersionMeta } from "@/lib/api/client";

import { CONFIG_DOMAINS, domainFromName, DOMAIN_SLUGS, isDomainSlug } from "./domains";
import { defaultFrom, formatPath, formatValue, groupByDomain, initialSelection, listErrors } from "./history";
import { exportFileName, parseImportFile } from "./transfer";

function meta(id: number, parent_id: number | null, active = false): ConfigVersionMeta {
  return { id, domain: "strategy", created_at: "2026-09-25T10:00:00Z", note: null, created_by: null, active, parent_id, schema_version: 1, changes: parent_id === null ? null : 1 };
}

// Newest first, as the API lists them. #9 is a rollback to #5 (its parent is #8).
const VERSIONS = [meta(9, 8, true), meta(8, 5), meta(5, 2), meta(2, null)];

describe("history selection", () => {
  it("defaults to comparing a version with its parent", () => {
    expect(initialSelection(VERSIONS)).toBe(9);
    expect(defaultFrom(VERSIONS, 9)).toBe(8);
    expect(defaultFrom(VERSIONS, 5)).toBe(2);
    expect(defaultFrom(VERSIONS, 2)).toBeNull();
  });

  it("falls back to the next older version when the parent is not listed", () => {
    expect(defaultFrom([meta(7, 3), meta(6, null)], 7)).toBe(6);
    expect(initialSelection([meta(4, 3), meta(3, null)])).toBe(4);
    expect(initialSelection([])).toBeNull();
  });

  it("formats paths and values", () => {
    expect(formatPath("/target_positions/0/band")).toBe("target_positions › 0 › band");
    expect(formatPath("/classifications/A~1B")).toBe("classifications › A/B");
    expect(formatPath("")).toBe("(whole body)");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue("x")).toBe('"x"');
    expect(formatValue(0.1)).toBe("0.1");
  });
});

describe("error lists", () => {
  it("reads import errors with their domain and field path", () => {
    const errors = listErrors({
      detail: [
        { loc: ["domains", "holding_profiles", "body", "profiles", 0, "ticker"], msg: "String should match pattern", type: "x" },
        { loc: ["domains", "strategy", "body"], msg: "Value error, position and cash targets sum to 1.1000, above 1", type: "value_error" },
        { loc: ["domains", "research"], msg: "unknown config domain 'research'", type: "value_error" },
        { loc: ["body", "domains"], msg: "Field required", type: "missing" },
        { loc: ["rules", 0, "position_weight", "value"], msg: "too big", type: "x" },
      ],
    });
    expect(errors).toEqual([
      { domain: "Holding profiles", path: "profiles.0.ticker", message: "String should match pattern" },
      { domain: "Strategy", path: "", message: "position and cash targets sum to 1.1000, above 1" },
      { domain: "research", path: "", message: "unknown config domain 'research'" },
      { domain: null, path: "domains", message: "Field required" },
      { domain: null, path: "rules.0.position_weight.value", message: "too big" },
    ]);
    expect(groupByDomain(errors).map((g) => [g.domain, g.errors.length])).toEqual([
      ["Holding profiles", 1],
      ["Strategy", 1],
      ["research", 1],
      [null, 2],
    ]);
    expect(listErrors({ detail: "No strategy version 4" })).toEqual([{ domain: null, path: "", message: "No strategy version 4" }]);
  });
});

describe("config domains and files", () => {
  it("covers every domain slug and maps export names", () => {
    expect(DOMAIN_SLUGS).toHaveLength(10);
    expect(domainFromName("holding_profiles")).toBe("holding-profiles");
    expect(domainFromName("research_profiles")).toBe("research-profiles");
    expect(CONFIG_DOMAINS["research-profiles"].editor).toBe("/config/research-profiles");
    expect(domainFromName("research")).toBeNull();
    expect(isDomainSlug("toString")).toBe(false);
    expect(CONFIG_DOMAINS.budget.editor).toBeNull();
  });

  it("names the export file by date", () => {
    expect(exportFileName(new Date(2026, 8, 5))).toBe("stewardai-config-2026-09-05.json");
  });

  it("checks an import file before sending it", () => {
    expect(parseImportFile("{")).toMatchObject({ ok: false });
    expect(parseImportFile("[]")).toMatchObject({ ok: false });
    expect(parseImportFile('{"format":"other","domains":{}}')).toMatchObject({ ok: false });
    expect(parseImportFile('{"domains":{}}')).toMatchObject({ ok: false });
    const ok = parseImportFile('{"format":"stewardai-config","domains":{"strategy":{"body":{"base_currency":"CZK"}}}}');
    expect(ok).toMatchObject({ ok: true, domains: ["strategy"] });
  });
});
