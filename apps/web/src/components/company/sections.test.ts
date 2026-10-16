import { describe, expect, it } from "vitest";

import { TABS } from "@/lib/company/company";

import { CHECKS_SLUG, COMPANY_SECTIONS, isSectionHome, sectionForSlug, slugFromPath } from "./sections";

describe("company sections", () => {
  it("numbers nine sections in sequence", () => {
    expect(COMPANY_SECTIONS.map((s) => s.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("places every tab route in exactly one section, except the checks route", () => {
    for (const t of TABS) {
      const owners = COMPANY_SECTIONS.filter((s) => s.routes.some((r) => r.slug === t.slug));
      expect(owners.length, t.slug || "overview").toBe(t.slug === CHECKS_SLUG ? 0 : 1);
    }
  });

  it("reads the slug from a pathname", () => {
    expect(slugFromPath("/companies/AAPL_US_EQ")).toBe("");
    expect(slugFromPath("/companies/AAPL_US_EQ/consensus")).toBe("consensus");
    expect(sectionForSlug("consensus")?.id).toBe("valuation");
    expect(sectionForSlug("checks")).toBeNull();
  });

  it("knows a section's home route", () => {
    const valuation = sectionForSlug("peers")!;
    expect(isSectionHome(valuation, "valuation")).toBe(true);
    expect(isSectionHome(valuation, "peers")).toBe(false);
  });
});
