import { describe, expect, it } from "vitest";

import { DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { CLUSTER_PAGES } from "@/lib/nav";

import { baseCommands, CONFIG_PAGES, filterCommands, holdingCommands, jobCommands, kpiCommands, OTHER_PAGES } from "./palette";

const BOARD_ROWS = [
  {
    ticker: "AAPL_US_EQ",
    name: "Apple",
    kpis: [
      { kpi: { id: "revenue-growth", metric: "revenue_growth_yoy" } },
      { kpi: { id: "max-position", metric: "position_weight" } },
    ],
  },
];

const commands = [
  ...baseCommands(),
  ...holdingCommands([{ ticker: "AAPL_US_EQ", name: "Apple" }]),
  ...kpiCommands(BOARD_ROWS),
];

describe("command palette", () => {
  it("reaches every primary destination and config editor", () => {
    const hrefs = new Set(commands.map((c) => c.href).filter(Boolean));
    for (const p of CLUSTER_PAGES) expect(hrefs).toContain(p.href);
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

describe("command palette: phase 4", () => {
  it("reaches the verification editor and the Activity page's sections", () => {
    expect(filterCommands(commands, "verification")[0].href).toBe("/config/verification");
    expect(filterCommands(commands, "claim verifier").map((c) => c.href)).toContain("/config/verification");
    expect(filterCommands(commands, "history verification")[0].href).toBe("/config/history/verification");
    expect(filterCommands(commands, "activity")[0].href).toBe("/activity");
    expect(filterCommands(commands, "job log")[0].href).toBe("/activity#job-log");
    expect(filterCommands(commands, "coverage gaps")[0].href).toBe("/activity#coverage");
    expect(filterCommands(commands, "source trust")[0].href).toBe("/activity#source-trust");
    expect(filterCommands(commands, "eval").map((c) => c.href)).toContain("/activity#eval-candidates");
    expect(filterCommands(commands, "missed runs").map((c) => c.href)).toContain("/activity#alerts");
  });
});

describe("command palette: phase 5", () => {
  it("opens the chat panel from the palette", () => {
    const [first] = filterCommands(commands, "open chat");
    expect(first).toMatchObject({ id: "ui:chat", ui: "open-chat" });
    expect(first.href).toBeUndefined();
    expect(filterCommands(commands, "chat").map((c) => c.href)).toContain("/chat");
  });

  it("reaches research, the monthly reviews, the report search and the research profiles", () => {
    expect(filterCommands(commands, "research")[0].href).toBe("/research");
    expect(filterCommands(commands, "monthly reviews")[0].href).toBe("/reviews");
    expect(filterCommands(commands, "search reports")[0].href).toBe("/reports?type=research");
    expect(filterCommands(commands, "research profiles").map((c) => c.href)).toContain("/config/research-profiles");
    expect(filterCommands(commands, "history research").map((c) => c.href)).toContain("/config/history/research-profiles");
  });

  it("runs the monthly review, the 13F load and research on a holding", () => {
    expect(filterCommands(commands, "monthly review now")[0].action).toEqual({ kind: "monthly-review" });
    expect(filterCommands(commands, "13f")[0].action).toEqual({ kind: "ownership-13f" });
    expect(filterCommands(commands, "research apple")[0].action).toEqual({ kind: "research", ticker: "AAPL_US_EQ" });
    expect(filterCommands(commands, "", 100).some((c) => c.id.startsWith("research:"))).toBe(false);
  });
});

describe("command palette: five-cluster IA", () => {
  const byHref = new Map(commands.map((c) => [c.href, c]));

  it("labels each destination with its Ledger cluster instead of a blanket \"Go to\"", () => {
    expect(byHref.get("/")?.group).toBe("Portfolio");
    expect(byHref.get("/transactions")?.group).toBe("Portfolio");
    expect(byHref.get("/theses")?.group).toBe("Research & Verification");
    expect(byHref.get("/review")?.group).toBe("Research & Verification");
    expect(byHref.get("/news")?.group).toBe("Research & Verification");
    expect(byHref.get("/escalations")?.group).toBe("Oversight");
    expect(byHref.get("/reports")?.group).toBe("Oversight");
    expect(byHref.get("/reviews")?.group).toBe("Oversight");
    expect(byHref.get("/activity")?.group).toBe("Oversight");
    expect(byHref.get("/config/strategy")?.group).toBe("Config");
    expect(commands.find((c) => c.id === "ui:chat")?.group).toBe("Copilot");
    expect(byHref.get("/chat")?.group).toBe("Copilot");
  });

  it("never lists Chat's full page as belonging to a cluster", () => {
    expect(commands.some((c) => c.href === "/chat" && c.group !== "Copilot")).toBe(false);
  });
});

describe("command palette: KPI depth", () => {
  it("resolves a typed metric and ticker straight to that KPI's row, not just the holding", () => {
    const [first] = filterCommands(commands, "revenue growth aapl");
    expect(first.href).toBe("/holdings/AAPL_US_EQ#kpi-row-revenue-growth");
    expect(first.label).toBe("Revenue growth YoY · Apple");
  });

  it("keeps per-KPI entries out of the empty-query list, like other per-holding entries", () => {
    expect(filterCommands(commands, "", 100).some((c) => c.id.startsWith("kpi:"))).toBe(false);
  });

  it("still finds the holding itself, distinct from its KPIs", () => {
    expect(filterCommands(commands, "aapl").map((c) => c.id)).toContain("holding:AAPL_US_EQ");
  });
});
