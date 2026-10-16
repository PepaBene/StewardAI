import { describe, expect, it } from "vitest";

import { costByJob, jobGroup, matrixRows, type JobGridRow } from "./overview";

const row = (jobType: string): JobGridRow => ({ jobType, cells: ["ok", "none"], last: null, runs: 1 });
const label = (t: string) => t.replaceAll("_", " ");

describe("jobGroup", () => {
  it("places known types in their group and the rest under Other", () => {
    expect(jobGroup("news_ingest")).toBe("News and alerts");
    expect(jobGroup("price_history")).toBe("Portfolio and prices");
    expect(jobGroup("brand_new_job")).toBe("Other");
  });

  it("groups an unlisted type by its human name, upkeep before filings", () => {
    expect(jobGroup("fund_probe", "Fund endpoint probes")).toBe("Upkeep and safety checks");
    expect(jobGroup("edinet", "EDINET filings")).toBe("Earnings and filings");
    expect(jobGroup("ai_news", "AI news search")).toBe("News and alerts");
    expect(jobGroup("x", "Something new")).toBe("Other");
  });
});

describe("matrixRows", () => {
  it("names each job, groups them in display order and keeps the given order inside a group", () => {
    const rows = matrixRows([row("research"), row("monitor"), row("mystery"), row("portfolio_sync"), row("news_ingest")], label);
    expect(rows.map((r) => r.label)).toEqual(["portfolio sync", "monitor", "news ingest", "research", "mystery"]);
    expect(rows.map((r) => r.group)).toEqual(["Portfolio and prices", "News and alerts", "News and alerts", "Analysis", "Other"]);
  });

  it("carries the day cells through and adds a note from the last run", () => {
    const last = { id: 1 } as never;
    const rows = matrixRows([{ ...row("monitor"), last }], label, () => "18:02 · 4s");
    expect(rows[0].cells).toEqual(["ok", "none"]);
    expect(rows[0].note).toBe("18:02 · 4s");
  });

  it("has no note without a last run", () => {
    expect(matrixRows([row("monitor")], label, () => "x")[0].note).toBeUndefined();
  });
});

describe("costByJob", () => {
  const data = [
    { job_type: "research", cost_usd: 1.5, runs: 3 },
    { job_type: "chat", cost_usd: 0, runs: 9 },
    { job_type: "thesis_verdicts", cost_usd: 4, runs: 2 },
    { job_type: "monitor", cost_usd: 0.2, runs: 50 },
  ];

  it("sorts largest first and drops zero-cost types", () => {
    expect(costByJob(data, label).map((c) => c.jobType)).toEqual(["thesis_verdicts", "research", "monitor"]);
  });

  it("caps the list", () => {
    expect(costByJob(data, label, 2)).toHaveLength(2);
  });
});
