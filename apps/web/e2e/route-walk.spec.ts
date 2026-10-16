import { mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { fixtureFor, VIEWPORTS } from "./fixtures";

/*
 * Route walk: opens every page under src/app/(app) at 1440x900 and 390x844 against BASE_URL and
 * writes e2e/out/route-metrics.json with, per route and viewport: document height, the number of
 * raw Trading212 ids visible, empty markers, whether "Unsaved changes" shows on load, horizontal
 * overflow, the document title and the h1.
 *
 * Sign-in: the owner creates the signed-in browser state once, with the password and MFA code:
 *   npx playwright codegen --save-storage=e2e/.auth/state.json $BASE_URL
 * (e2e/.auth is gitignored). Never run this against production without a read-only session.
 */

const APP_DIR = path.join(process.cwd(), "src", "app", "(app)");
const OUT_DIR = path.join(process.cwd(), "e2e", "out");

/** Every `page.tsx` under the (app) route group, as a URL pattern like `/companies/[ticker]/dividends`. */
function appRoutes(dir = APP_DIR, prefix = ""): string[] {
  const routes: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      // Route groups "(x)" do not appear in the URL; private folders "_x" are not routes.
      if (entry.startsWith("_")) continue;
      const segment = entry.startsWith("(") && entry.endsWith(")") ? "" : `/${entry}`;
      routes.push(...appRoutes(full, prefix + segment));
    } else if (entry === "page.tsx") {
      routes.push(prefix === "" ? "/" : prefix);
    }
  }
  return routes.sort();
}

/** Fills `[segment]` parts from the fixtures; null when one has no fixture. */
function resolveRoute(pattern: string): string | null {
  let missing = false;
  const url = pattern.replace(/\[([^\]]+)\]/g, (_, segment: string) => {
    const value = fixtureFor(segment);
    if (value === null) missing = true;
    return encodeURIComponent(value ?? segment);
  });
  return missing ? null : url;
}

type Metric = {
  route: string;
  pattern: string;
  viewport: string;
  status: number | null;
  finalUrl: string;
  documentHeight: number;
  rawIdCount: number;
  emptyMarkers: { dashes: number; unknown: number };
  unsavedChangesOnLoad: boolean;
  horizontalOverflow: boolean;
  title: string;
  h1: string | null;
};
type Skipped = { pattern: string; reason: string };

const metrics: Metric[] = [];
const skipped: Skipped[] = [];

const RAW_ID = /\b[A-Za-z0-9]+_[A-Z]{2}_EQ\b|[A-Za-z]_EQ\b/g;

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  if (!process.env.BASE_URL) throw new Error("Set BASE_URL to the deployment to walk (no default, to avoid hitting production by accident).");
});

for (const pattern of appRoutes()) {
  const url = resolveRoute(pattern);
  if (url === null) {
    skipped.push({ pattern, reason: "no fixture for a dynamic segment" });
    continue;
  }
  for (const vp of VIEWPORTS) {
    test(`${vp.name} ${url}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      const response = await page.goto(url, { waitUntil: "networkidle" }).catch(() => page.goto(url));
      // Pages fill in after the first paint; give the data requests a moment to settle.
      await page.waitForTimeout(1500);
      const data = await page.evaluate(() => {
        const text = document.body.innerText;
        const cells = Array.from(document.querySelectorAll("td, dd, [role='cell']"));
        return {
          text,
          documentHeight: document.documentElement.scrollHeight,
          dashes: cells.filter((c) => (c as HTMLElement).innerText.trim() === "—").length,
          overflow: document.documentElement.scrollWidth > window.innerWidth,
          title: document.title,
          h1: document.querySelector("h1")?.textContent?.trim() ?? null,
        };
      });
      const unsaved = await page.getByText("Unsaved changes", { exact: false }).first().isVisible().catch(() => false);
      metrics.push({
        route: url,
        pattern,
        viewport: vp.name,
        status: response?.status() ?? null,
        finalUrl: new URL(page.url()).pathname,
        documentHeight: data.documentHeight,
        rawIdCount: (data.text.match(RAW_ID) ?? []).length,
        emptyMarkers: { dashes: data.dashes, unknown: (data.text.match(/\bUnknown\b/g) ?? []).length },
        unsavedChangesOnLoad: unsaved,
        horizontalOverflow: data.overflow,
        title: data.title,
        h1: data.h1,
      });
      // The walk records; it only fails when the page did not load at all.
      expect(response?.status() ?? 0, `${url} loaded`).toBeLessThan(500);
    });
  }
}

test.afterAll(() => {
  mkdirSync(OUT_DIR, { recursive: true });
  const duplicates = (key: "title" | "h1") => {
    const seen = new Map<string, string[]>();
    for (const m of metrics.filter((x) => x.viewport === "desktop")) {
      const v = m[key];
      if (v) seen.set(v, [...(seen.get(v) ?? []), m.route]);
    }
    return Object.fromEntries([...seen].filter(([, routes]) => routes.length > 1));
  };
  writeFileSync(
    path.join(OUT_DIR, "route-metrics.json"),
    JSON.stringify({ baseUrl: process.env.BASE_URL, at: new Date().toISOString(), metrics, skipped, duplicateTitles: duplicates("title"), duplicateH1s: duplicates("h1") }, null, 2),
  );
});
