import { describe, expect, it } from "vitest";

import type { ThesesConfig } from "@/lib/api/client";

import {
  EMPTY_RIGOR,
  EMPTY_TARGET,
  lines,
  newKpi,
  parseThreshold,
  percentToFraction,
  preMortemAsExits,
  rigorGaps,
  slugFromMetric,
  thesesFromBody,
  thesesToBody,
} from "./theses-form";

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
      price_target: {
        sell_target: 260,
        buy_target: 180,
        currency: "USD",
        method: "DCF",
        set_on: "2026-10-01",
      },
      implied_growth: 0.085,
      base_rate: { share_achieved: 0.2, comparison: "US large caps, 10-year", source: "own screen" },
      pre_mortem: ["Services regulation", "iPhone share loss", "Margin squeeze"],
      variant_perception: { belief: "Services outgrow the street", disproof: "Two quarters below 8 %" },
      conviction: 4,
      main_reason: "quality",
    },
  ],
};

describe("theses form", () => {
  it("round-trips the API body", () => {
    expect(thesesToBody(thesesFromBody(BODY))).toEqual(BODY);
  });

  it("sends the thesis requirements as null or empty when they are blank", () => {
    const forms = thesesFromBody(BODY);
    forms[0].rigor = { ...EMPTY_RIGOR };
    expect(thesesToBody(forms).theses![0]).toMatchObject({
      implied_growth: null,
      base_rate: null,
      pre_mortem: [],
      variant_perception: null,
      conviction: null,
      main_reason: null,
    });
  });

  it("reads growth and the base rate as percent and sends fractions", () => {
    expect(percentToFraction("8,5")).toBeCloseTo(0.085);
    expect(percentToFraction("")).toBeNull();
    const forms = thesesFromBody(BODY);
    expect(forms[0].rigor.implied_growth).toBe("8.5");
    expect(forms[0].rigor.base_share).toBe("20");
  });

  it("names what the thesis still lacks", () => {
    expect(rigorGaps(EMPTY_RIGOR)).toHaveLength(6);
    expect(rigorGaps(thesesFromBody(BODY)[0].rigor)).toEqual([]);
    expect(rigorGaps({ ...thesesFromBody(BODY)[0].rigor, pre_mortem: "a\nb" })).toEqual(["three pre-mortem reasons"]);
  });

  it("offers each pre-mortem reason as an exit condition once", () => {
    expect(preMortemAsExits("Buybacks stop\nMargin squeeze", "Margin squeeze\nRegulation")).toBe(
      "Buybacks stop\nMargin squeeze\nRegulation",
    );
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
    expect(thesesToBody(forms).theses![0].kpis![1]).toMatchObject({
      id: "gross-margin",
      threshold: null,
    });
  });

  it("sends a blank price target as none and a partial one for the API to check", () => {
    const forms = thesesFromBody(BODY);
    forms[0].target = { ...EMPTY_TARGET };
    expect(thesesToBody(forms).theses![0].price_target).toBeNull();
    forms[0].target = { ...EMPTY_TARGET, sell_target: "300", currency: "usd" };
    expect(thesesToBody(forms).theses![0].price_target).toEqual({
      sell_target: 300,
      buy_target: null,
      currency: "USD",
      method: "",
      set_on: "",
    });
  });

  it("splits lists and builds ids", () => {
    expect(lines(" a \n\n b\n")).toEqual(["a", "b"]);
    expect(slugFromMetric("Revenue_Growth_YoY")).toBe("revenue-growth-yoy");
    expect(thesesFromBody(null)).toEqual([]);
  });
});
