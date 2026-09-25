import { describe, expect, it } from "vitest";

import { DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { SECTIONS } from "@/lib/nav";

import { baseCommands, CONFIG_PAGES, filterCommands, holdingCommands, jobCommands, OTHER_PAGES } from "./palette";

const commands = [...baseCommands(), ...holdingCommands([{ ticker: "AAPL_US_EQ", name: "Apple" }])];

describe("command palette", () => {
  it("reaches every primary destination and config editor", () => {
    const hrefs = new Set(commands.map((c) => c.href).filter(Boolean));
    for (const s of SECTIONS) expect(hrefs).toContain(`/${s.slug}`);
    for (const p of CONFIG_PAGES) expect(hrefs).toContain(p.href);
    for (const p of OTHER_PAGES) expect(hrefs).toContain(p.href);
    for (const slug of DOMAIN_SLUGS) expect(hrefs).toContain(historyHref(slug));
    for (const href of ["/config/strategy", "/config/jobs", "/transactions", "/theses", "/review"]) {
      expect(hrefs).toContain(href);
    }
  });

  it("finds the new pages by typing", () => {
    expect(filterCommands(commands, "dividends")[0].href).toBe("/transactions");
    expect(filterCommands(commands, "history strategy")[0].href).toBe("/config/history/strategy");
    expect(filterCommands(commands, "cron").map((c) => c.href)).toContain("/config/jobs");
  });

  it("finds the thesis board and the review queue by typing", () => {
    expect(filterCommands(commands, "thesis board")[0].href).toBe("/theses");
    expect(filterCommands(commands, "breach").map((c) => c.href)).toContain("/theses");
    expect(filterCommands(commands, "review queue")[0].href).toBe("/review");
    expect(filterCommands(commands, "quarantined").map((c) => c.href)).toContain("/review");
    expect(filterCommands(commands, "review apple")[0].href).toBe("/review?ticker=AAPL_US_EQ");
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

  it("hides per-holding actions and queues until something is typed", () => {
    const shown = filterCommands(commands, "", 100);
    expect(
      shown.some((c) => c.id.startsWith("earnings:") || c.id.startsWith("review:") || c.id.startsWith("reference:")),
    ).toBe(false);
  });

  it("reaches the phase 2b pages and actions", () => {
    expect(filterCommands(commands, "exchange calendar")[0].href).toBe("/config/calendar");
    expect(filterCommands(commands, "holidays").map((c) => c.href)).toContain("/config/calendar");
    expect(filterCommands(commands, "reference gaps")[0].href).toBe("/status#reference-gaps");
    expect(filterCommands(commands, "stooq symbol").map((c) => c.href)).toContain("/config/holding-profiles");
    expect(filterCommands(commands, "benchmarks").map((c) => c.href)).toContain("/config/jobs");
    const actions = filterCommands(commands, "now", 50).map((c) => c.action);
    expect(actions).toContainEqual({ kind: "reference-data" });
    expect(actions).toContainEqual({ kind: "price-history" });
    expect(actions).toContainEqual({ kind: "holiday-refresh" });
    expect(filterCommands(commands, "reference apple")[0].action).toEqual({ kind: "reference-data", ticker: "AAPL_US_EQ" });
  });
});

describe("command palette: phase 3", () => {
  it("reaches the disputed stories and the impact notes", () => {
    expect(filterCommands(commands, "disputed")[0].href).toBe("/news/disputed");
    expect(filterCommands(commands, "contradicted").map((c) => c.href)).toContain("/news/disputed");
    expect(filterCommands(commands, "escalations").map((c) => c.href)).toContain("/escalations");
    expect(filterCommands(commands, "impact notes")[0].href).toBe("/escalations");
    expect(filterCommands(commands, "macro surprise").map((c) => c.href)).toContain("/config/event-rules");
  });

  it("runs the macro collection", () => {
    expect(filterCommands(commands, "macro now")[0].action).toEqual({ kind: "macro-collect" });
  });

  it("opens a job run by number", () => {
    expect(jobCommands("42")).toEqual([expect.objectContaining({ href: "/jobs/42", label: "Job #42" })]);
    expect(jobCommands(" job 42 ")[0].href).toBe("/jobs/42");
    expect(jobCommands("#7")[0].href).toBe("/jobs/7");
    expect(jobCommands("0")).toEqual([]);
    expect(jobCommands("news")).toEqual([]);
  });
});
