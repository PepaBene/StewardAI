import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { DESTINATIONS, GLOBAL_ROUTES, NAV_PAGES, REDIRECTS, redirectTarget, resolveNav } from "./nav";

const APP_DIR = join(__dirname, "..", "app", "(app)");

/** Every page route under app/(app), with dynamic segments filled by a sample value. */
function appRoutes(dir = APP_DIR, prefix = ""): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      const segment = entry.startsWith("[") ? "sample" : entry;
      out.push(...appRoutes(full, `${prefix}/${segment}`));
    } else if (entry === "page.tsx") {
      out.push(prefix || "/");
    }
  }
  return out;
}

const owner = (path: string) => resolveNav(path);
const destinationOf = (path: string) => owner(path)?.destination.id;
const tabOf = (path: string) => owner(path)?.tab.label;

describe("information architecture", () => {
  it("gives every route in app/(app) exactly one home: a tab, a redirect or a global route", () => {
    const homeless = appRoutes().filter(
      (path) =>
        !owner(path) &&
        !redirectTarget(path) &&
        !GLOBAL_ROUTES.some((g) => path === g || path.startsWith(`${g}/`)),
    );
    expect(homeless).toEqual([]);
  });

  it("points every tab and every redirect at a route that exists", () => {
    const routes = new Set(appRoutes());
    for (const p of NAV_PAGES) expect(routes, p.href).toContain(p.href);
    for (const r of REDIRECTS) {
      const target = r.destination.split("?")[0].replace(/:\w+/g, "sample");
      expect(routes, r.source).toContain(target);
    }
  });

  it("has seven destinations plus Settings, and no destination for Chat", () => {
    expect(DESTINATIONS.map((d) => d.label)).toEqual([
      "Portfolio",
      "Companies",
      "Research",
      "Markets",
      "Briefings",
      "Inbox",
      "System",
      "Settings",
    ]);
    expect(owner("/chat")).toBeNull();
  });

  it("lets the most specific tab win", () => {
    expect(tabOf("/")).toBe("Overview");
    expect(tabOf("/holdings")).toBe("Holdings");
    expect(tabOf("/transactions")).toBe("Holdings");
    expect(destinationOf("/holdings/AAPL_US_EQ")).toBe("companies");
    expect(tabOf("/risk")).toBe("Risk");
    expect(tabOf("/risk/tail")).toBe("Risk");
    expect(tabOf("/risk/whatif")).toBe("Decide");
    expect(tabOf("/xray/proposed")).toBe("Decide");
    expect(tabOf("/xray/overlap")).toBe("Allocation");
    expect(tabOf("/funds")).toBe("Holdings");
    expect(destinationOf("/funds/IE00B5BMR087")).toBe("companies");
    expect(tabOf("/disclosures/coverage")).toBe("Data quality");
    expect(tabOf("/disclosures/insiders")).toBe("Filings");
    expect(tabOf("/jobs/42")).toBe("Activity");
    expect(destinationOf("/jobsx")).toBeUndefined();
  });

  it("never confuses /reviews (monthly reviews) with /review (the figures queue)", () => {
    expect(destinationOf("/reviews")).toBe("briefings");
    expect(destinationOf("/review")).toBe("inbox");
  });

  it("sends the old queue pages to the Inbox with their kind chip selected", () => {
    expect(redirectTarget("/escalations")).toBe("/inbox?kind=escalations");
    expect(redirectTarget("/escalations/12")).toBe("/inbox?kind=escalations&item=12");
    expect(redirectTarget("/review")).toBe("/inbox?kind=figures");
    expect(redirectTarget("/config")).toBe("/config/strategy");
    expect(redirectTarget("/reviews")).toBeNull();
  });

  it("groups Settings into its four menus, covering every editor", () => {
    const settings = DESTINATIONS.find((d) => d.id === "settings");
    expect(settings?.groups?.map((g) => g.label)).toEqual([
      "Investment rules",
      "Sources and data",
      "Automation",
      "History and export",
    ]);
    expect(destinationOf("/config/history/strategy")).toBe("settings");
  });
});
