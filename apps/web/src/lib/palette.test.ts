import { describe, expect, it } from "vitest";

import { DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { SECTIONS } from "@/lib/nav";

import { baseCommands, CONFIG_PAGES, filterCommands, holdingCommands, OTHER_PAGES } from "./palette";

const commands = [...baseCommands(), ...holdingCommands([{ ticker: "AAPL_US_EQ", name: "Apple" }])];

describe("command palette", () => {
  it("reaches every primary destination and config editor", () => {
    const hrefs = new Set(commands.map((c) => c.href).filter(Boolean));
    for (const s of SECTIONS) expect(hrefs).toContain(`/${s.slug}`);
    for (const p of CONFIG_PAGES) expect(hrefs).toContain(p.href);
    for (const p of OTHER_PAGES) expect(hrefs).toContain(p.href);
    for (const slug of DOMAIN_SLUGS) expect(hrefs).toContain(historyHref(slug));
    for (const href of ["/config/strategy", "/config/jobs", "/transactions"]) expect(hrefs).toContain(href);
  });

  it("finds the new pages by typing", () => {
    expect(filterCommands(commands, "dividends")[0].href).toBe("/transactions");
    expect(filterCommands(commands, "history strategy")[0].href).toBe("/config/history/strategy");
    expect(filterCommands(commands, "cron").map((c) => c.href)).toContain("/config/jobs");
  });

  it("finds the re-run earnings check action by typing", () => {
    const [first] = filterCommands(commands, "earnings apple");
    expect(first.action).toEqual({ kind: "earnings-check", ticker: "AAPL_US_EQ" });
    expect(filterCommands(commands, "aapl").map((c) => c.id)).toContain("holding:AAPL_US_EQ");
  });

  it("ranks label prefix matches first and requires every word", () => {
    expect(filterCommands(commands, "news")[0].label).toBe("News");
    expect(filterCommands(commands, "news zebra")).toEqual([]);
  });

  it("hides per-holding actions until something is typed", () => {
    expect(filterCommands(commands, "", 100).some((c) => c.id.startsWith("earnings:"))).toBe(false);
  });
});
