import { describe, expect, it } from "vitest";

import { CLUSTER_PAGES, CLUSTERS, isClusterActive, isPageActive } from "./nav";

function cluster(id: (typeof CLUSTERS)[number]["id"]) {
  const c = CLUSTERS.find((c) => c.id === id);
  if (!c) throw new Error(`no such cluster: ${id}`);
  return c;
}

describe("cluster highlighting", () => {
  it("marks Portfolio active on the overview, holdings and transactions, not on Config", () => {
    const portfolio = cluster("portfolio");
    expect(isClusterActive(portfolio, "/")).toBe(true);
    expect(isClusterActive(portfolio, "/holdings")).toBe(true);
    expect(isClusterActive(portfolio, "/holdings/AAPL_US_EQ")).toBe(true);
    expect(isClusterActive(portfolio, "/transactions")).toBe(true);
    expect(isClusterActive(portfolio, "/transactionsx")).toBe(false);
    expect(isClusterActive(portfolio, "/theses")).toBe(false);
    expect(isClusterActive(cluster("config"), "/theses")).toBe(false);
  });

  it("groups Theses, Review, Research, News and fact-evidence pages under Research & Verification", () => {
    const research = cluster("research");
    expect(isClusterActive(research, "/theses")).toBe(true);
    expect(isClusterActive(research, "/review")).toBe(true);
    // "/reviews" (monthly compliance) must never match "/review" (the fact review queue).
    expect(isClusterActive(research, "/reviews")).toBe(false);
    expect(isClusterActive(research, "/research/4")).toBe(true);
    expect(isClusterActive(research, "/news/disputed")).toBe(true);
    expect(isClusterActive(research, "/evidence/123")).toBe(true);
    expect(isClusterActive(research, "/escalations")).toBe(false);
  });

  it("groups Escalations, Reports, Reviews, Activity, job runs and API status under Oversight", () => {
    const oversight = cluster("oversight");
    expect(isClusterActive(oversight, "/escalations")).toBe(true);
    expect(isClusterActive(oversight, "/escalations/12")).toBe(true);
    expect(isClusterActive(oversight, "/reports")).toBe(true);
    expect(isClusterActive(oversight, "/reviews")).toBe(true);
    expect(isClusterActive(oversight, "/reviews/3")).toBe(true);
    expect(isClusterActive(oversight, "/activity")).toBe(true);
    expect(isClusterActive(oversight, "/jobs/42")).toBe(true);
    expect(isClusterActive(oversight, "/jobsx")).toBe(false);
    expect(isClusterActive(oversight, "/status")).toBe(true);
  });

  it("marks Config active on every sub-editor and its history, not on other clusters' pages", () => {
    const config = cluster("config");
    expect(isClusterActive(config, "/config")).toBe(true);
    expect(isClusterActive(config, "/config/verification")).toBe(true);
    expect(isClusterActive(config, "/config/history/strategy")).toBe(true);
    expect(isClusterActive(config, "/config/research-profiles")).toBe(true);
    expect(isClusterActive(cluster("research"), "/config/research-profiles")).toBe(false);
  });

  it("has no cluster for Chat: it stays a global slide-over, never a nav destination", () => {
    for (const c of CLUSTERS) {
      expect(isClusterActive(c, "/chat")).toBe(false);
    }
  });

  it("checks one page at a time, e.g. for a dropdown menu's current-page highlight", () => {
    expect(isPageActive("/holdings", "/holdings")).toBe(true);
    expect(isPageActive("/holdings", "/transactions")).toBe(false);
  });

  it("flattens every cluster's pages with its cluster label attached", () => {
    const byHref = new Map(CLUSTER_PAGES.map((p) => [p.href, p]));
    expect(byHref.get("/")?.cluster).toBe("Portfolio");
    expect(byHref.get("/theses")?.cluster).toBe("Research & Verification");
    expect(byHref.get("/reports")?.cluster).toBe("Oversight");
    expect(byHref.get("/config")?.cluster).toBe("Config");
  });
});
