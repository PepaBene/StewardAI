import { describe, expect, it } from "vitest";

import { rollup, type CoverageCell, type CoverageStage } from "@/lib/disclosure/coverage";
import { buybackImpact, debtImpact, dilutionImpact, formatTerm, type TermVersion } from "@/lib/disclosure/deals";
import { maturityCheck } from "@/lib/disclosure/financials";
import { netBars, type NetRowOut } from "@/lib/disclosure/insiders";
import { httpFailure, outcomeOf, resolveLoaded } from "@/lib/disclosure/keyed";
import { PAGE_DATASET, releaseStatus } from "@/lib/disclosure/release";

const cell = (o: Partial<CoverageCell>) => ({ dataset: "ownership", label: "Ownership", jurisdiction: "US", tickers: 2, covered: 1, uncovered: 1, stale: 0, denominator_value: "100", covered_value: "40", first_known_at: null, last_known_at: null, unit: "usd", source: "s", method: "m", blind_spots: [], ...o }) as unknown as CoverageCell;

describe("missing is not zero", () => {
  it("draws a role with no trades as no bar", () => {
    const rows: NetRowOut[] = [{ role: "officer", window: 90, netValue: 0, buyValue: 0, sellValue: 0, insiders: 0, trades: 0, planExcluded: 0, planUnknown: 0, bundled: 0 }];
    const bars = netBars(rows, 90);
    expect(bars.find((b) => b.label.startsWith("Officers"))).toMatchObject({ value: null, label: "Officers (no trades)" });
  });

  it("states no share when any cell has an unknown denominator, and does not sum it as zero", () => {
    const [r] = rollup([cell({}), cell({ jurisdiction: "UK", denominator_value: undefined })]);
    expect(r.share).toBeNull();
    expect(r.shareUnknown).toBe(true);
    expect(r.denominator).toBe(100);
    const [ok] = rollup([cell({}), cell({ jurisdiction: "UK", denominator_value: "100", covered_value: "60" })]);
    expect(ok.share).toBeCloseTo(0.5);
  });

  it("flags gross-of-refinancing for any deal type when the refinanced amount is unknown", () => {
    const tv = { id: 1, currency: "USD", terms: [{ name: "debt_amount", value: "100", state: "stated" }] } as unknown as TermVersion;
    const i = debtImpact("acquisition", tv, { enterpriseValueBefore: 1000 });
    expect(i.value).toBe("0.1");
    expect(i.flags).toContain("gross_of_refinancing");
  });
});

describe("TypeScript copies match the Python rules", () => {
  it("formats 0.07 as 7%, not 7.0%", () => {
    expect(formatTerm("stake_pct", "0.07", "USD")).toBe("7%");
    expect(formatTerm("stake_pct", "0.0725", "USD")).toBe("7.3%");
  });

  it("allows rounding on maturities and sends a negative bucket to review", () => {
    expect(maturityCheck([{ label: "1y", value: 100 }, { label: "2y", value: 99 }], 200).status).toBe("pass");
    expect(maturityCheck([{ label: "1y", value: 100 }, { label: "2y", value: -5 }], 95).status).toBe("needs_review");
  });

  it("guards non-positive share counts and authorizations", () => {
    const tv = { id: 1, currency: "USD", terms: [{ name: "exchange_ratio", value: "1.5", state: "stated" }, { name: "authorized_amount", value: "0" }, { name: "executed_amount", value: "5" }] } as unknown as TermVersion;
    const d = dilutionImpact(tv, { acquirerShares: 0, targetShares: 100 });
    expect(d.value ?? null).toBeNull();
    const b = buybackImpact(tv);
    expect(b.value ?? null).toBeNull();
  });
});

describe("keyed results", () => {
  const ok = { key: "A|2026-01-01", data: { n: 1 }, error: null };
  it("shows data only for the key that produced it", () => {
    expect(resolveLoaded(ok, "A|2026-01-01")).toEqual({ state: "ready", data: { n: 1 } });
    expect(resolveLoaded(ok, "A|2026-02-01")).toEqual({ state: "loading" });
    expect(resolveLoaded(ok, null)).toBeNull();
  });
  it("shows an explicit error for the failed key, never the old data", () => {
    const failed = { key: "A|2026-02-01", data: null, error: "boom" };
    expect(resolveLoaded(failed, "A|2026-02-01")).toEqual({ state: "error", message: "boom" });
  });
  it("turns an HTTP error into an outcome, not an empty answer", () => {
    expect(outcomeOf("The portfolio", { response: { status: 500 } })).toEqual({ error: httpFailure("The portfolio", 500) });
    expect(outcomeOf("x", { data: { a: 1 }, response: { status: 200 } })).toEqual({ data: { a: 1 } });
  });
});

describe("release order", () => {
  const stages = [
    { order: 2, stage: "Ownership and insiders", datasets: ["ownership", "insiders"], gates: {}, state: "gates_met" },
    { order: 3, stage: "Relationships and deals", datasets: ["dependencies", "deals"], gates: {}, state: "gates_open" },
  ] as CoverageStage[];
  it("maps each page to its stage", () => {
    expect(releaseStatus(stages, "ownership")?.released).toBe(true);
    expect(releaseStatus(stages, "network")).toMatchObject({ released: false, stage: "Relationships and deals" });
    expect(releaseStatus(stages, "deals")?.released).toBe(false);
    expect(releaseStatus(stages, "estimates")).toBeNull();
    expect(Object.keys(PAGE_DATASET)).toHaveLength(5);
  });
});
