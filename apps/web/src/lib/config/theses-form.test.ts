import { describe, expect, it } from "vitest";

import type { ThesesConfig } from "@/lib/api/client";

import { lines, newKpi, parseThreshold, slugFromMetric, thesesFromBody, thesesToBody } from "./theses-form";

const BODY: ThesesConfig = {
  schema_version: 1,
  theses: [
    {
      ticker: "AAPL_US_EQ",
      why_owned: "Services growth",
      exit_conditions: ["Services growth below 5 %", "Buybacks stop"],
      review_date: "2027-01-31",
      kpis: [
        {
          id: "growth",
          metric: "revenue_growth_yoy",
          operator: ">=",
          threshold: 5,
          unit: "%",
          basis: "reported",
          period: "quarter",
          description: "",
        },
      ],
    },
  ],
};

describe("theses form", () => {
  it("round-trips the API body", () => {
    expect(thesesToBody(thesesFromBody(BODY))).toEqual(BODY);
  });

  it("edits a threshold as text and sends it as a number", () => {
    const forms = thesesFromBody(BODY);
    forms[0].kpis[0].threshold = " 12,5 ";
    expect(thesesToBody(forms).theses![0].kpis![0].threshold).toBe(12.5);
  });

  it("sends a missing threshold as null so the API reports it", () => {
    expect(parseThreshold("")).toBeNull();
    expect(parseThreshold("abc")).toBeNull();
    const forms = thesesFromBody(BODY);
    forms[0].kpis.push(newKpi("gross_margin"));
    expect(thesesToBody(forms).theses![0].kpis![1]).toMatchObject({ id: "gross-margin", threshold: null });
  });

  it("splits lists and builds ids", () => {
    expect(lines(" a \n\n b\n")).toEqual(["a", "b"]);
    expect(slugFromMetric("Revenue_Growth_YoY")).toBe("revenue-growth-yoy");
    expect(thesesFromBody(null)).toEqual([]);
  });
});
