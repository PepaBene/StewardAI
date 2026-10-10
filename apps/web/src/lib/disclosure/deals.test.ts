import { describe, expect, it } from "vitest";

import { bridgeParts, describeChange, filterDeals, impactWaterfalls, portfolioExposure, premiumImpact, resolveState, timelineLayout, transitionProblem, versionSummary, type Deal, type DealTransition } from "@/lib/disclosure/deals";
import { buildDeal, RAW_DEALS, sampleDeals, samplePortfolioDeals } from "@/lib/disclosure/fixtures-deals";

const ms = (day: string) => Date.parse(`${day}T23:59:59Z`);
const deal = (id: number, day: string, offer?: number): Deal => buildDeal(RAW_DEALS.find((d) => d.id === id)!, ms(day), offer)!;

const tr = (id: number, state: string, eff: string, known: string, evidence: number | null = id): DealTransition => ({
  id, state: state as DealTransition["state"], effective_at: `${eff}T12:00:00Z`, known_at: `${known}T12:00:00Z`, evidence_id: evidence as number,
  document_version_id: 1, source_url: null, quote: null, span_start: null, span_end: null, rumor_source: null, note: null,
});

describe("state machine", () => {
  it("rejects an illegal move with its reason and keeps a terminated deal in history", () => {
    const r = resolveState("acquisition", [tr(1, "announced", "2026-10-05", "2026-10-05"), tr(2, "terminated", "2026-11-01", "2026-11-03"), tr(3, "closed", "2026-12-01", "2026-12-01")], ms("2026-12-31"));
    expect(r.state).toBe("terminated");
    expect(r.applied.map((t) => t.state)).toEqual(["announced", "terminated"]);
    expect(r.rejected).toEqual([{ id: 3, state: "closed", reason: "terminated cannot move to closed (a closed or terminated deal is final)" }]);
  });

  it("shows no future knowledge at three cutoffs", () => {
    const ts = [tr(1, "announced", "2026-10-05", "2026-10-05"), tr(2, "definitive", "2026-10-20", "2026-10-20"), tr(3, "terminated", "2026-11-01", "2026-11-03")];
    expect([ms("2026-10-01"), ms("2026-10-10"), ms("2026-11-02"), ms("2026-11-04")].map((c) => resolveState("acquisition", ts, c).state)).toEqual([null, "announced", "definitive", "terminated"]);
  });

  it("requires evidence, a named rumor source, and a legal first state", () => {
    expect(resolveState("acquisition", [tr(1, "announced", "2026-10-05", "2026-10-05", null)], ms("2026-12-01")).rejected[0].reason).toMatch(/evidence is mandatory/);
    expect(resolveState("acquisition", [tr(1, "rumored", "2026-09-01", "2026-09-01")], ms("2026-12-01")).rejected[0].reason).toMatch(/must name its source/);
    expect(transitionProblem("acquisition", null, "closed")).toMatch(/needs an earlier state/);
    expect(transitionProblem("buyback", "announced", "contested")).toMatch(/cannot be contested/);
    expect(transitionProblem("acquisition", "announced", "announced")).toMatch(/amendment adds a term version/);
  });

  it("rejects a change whose effective date runs backwards", () => {
    const r = resolveState("acquisition", [tr(1, "announced", "2026-10-05", "2026-10-05"), tr(2, "definitive", "2026-10-01", "2026-10-06")], ms("2026-11-01"));
    expect(r.state).toBe("announced");
    expect(r.rejected[0].reason).toMatch(/before the previous state/);
  });
});

describe("premium", () => {
  it("uses the correctly dated unaffected price and rejects one dated on or after the announcement", () => {
    const v = deal(1, "2026-03-10").term_versions[0];
    const ok = premiumImpact(v, "2026-03-02");
    expect(Number(ok.value)).toBeCloseTo(42 / 33 - 1, 6);
    expect(premiumImpact({ ...v, unaffected_date: "2026-03-02" }, "2026-03-02").reason).toMatch(/on or after the announcement/);
    expect(premiumImpact({ ...v, unaffected_date: "2026-02-01" }, "2026-03-02").flags).toEqual(["stale_unaffected_price"]);
    expect(premiumImpact({ ...v, unaffected_date: null }, "2026-03-02").value).toBeNull();
    expect(premiumImpact(undefined, "2026-03-02").value).toBeNull();
  });

  it("labels an owner scenario as its own kind", () => {
    const i = deal(1, "2026-03-10", 40).impacts.find((x) => x.origin === "owner_scenario")!;
    expect(i.label).toBe("Premium in your scenario");
    expect(Number(i.value)).toBeCloseTo(40 / 33 - 1, 6);
  });
});

describe("sample deals as known on a date", () => {
  it("shows only the deals and versions known by each of three cutoffs", () => {
    expect(sampleDeals(ms("2026-01-10")).deals).toEqual([]);
    expect(sampleDeals(ms("2026-01-20")).deals.map((d) => d.id)).toEqual([3]);
    const mid = sampleDeals(ms("2026-03-25"));
    expect(mid.deals.map((d) => d.id)).toEqual([1, 2, 3, 4]);
    expect(mid.deals.find((d) => d.id === 4)!.state).toBe("announced"); // the withdrawal is not yet known
    expect(deal(4, "2026-04-02").state).toBe("terminated");
    expect(deal(4, "2026-04-02").transitions.map((t) => t.state)).toEqual(["rumored", "announced", "terminated"]);
  });

  it("adds a term version for an amendment and describes what changed", () => {
    expect(deal(1, "2026-04-01").term_versions).toHaveLength(2); // v1 announced + v1 projection
    const after = deal(1, "2026-04-20");
    const v2 = after.term_versions.find((v) => v.version_no === 2)!;
    expect(describeChange(v2.changes[0], v2.currency)).toBe("price per share USD 42.00 → USD 44.50");
    expect(versionSummary(v2)).toMatch(/^price per share USD 42\.00 → USD 44\.50 \(\+2 more\)$/);
    expect(after.latest_version_id).toBe(102);
    const prem = after.impacts.find((i) => i.kind === "premium")!;
    expect(prem.term_version_ids).toEqual([102]);
    expect(Number(prem.value)).toBeCloseTo(44.5 / 33 - 1, 6);
  });

  it("keeps closing dates apart and lists the one not yet known", () => {
    const c = deal(1, "2026-09-30").closing!;
    expect([c.legal_closing_date, c.broker_posting_date, c.first_reporting_period_end]).toEqual(["2026-07-31", "2026-08-03", null]);
    expect(c.missing).toEqual(["first_reporting_period_end"]);
    expect(deal(1, "2026-10-25").closing!.missing).toEqual([]);
  });

  it("labels management projections apart from announced terms", () => {
    const d = deal(1, "2026-03-10");
    const syn = d.impacts.find((i) => i.kind === "synergies")!;
    expect(syn.origin).toBe("management_projection");
    expect(d.term_versions.filter((v) => v.origin === "management_projection")).toHaveLength(1);
    expect(d.bridge.segments.every((s) => s.amount === null || s.state === "stated")).toBe(true);
  });

  it("buyback: an authorization alone gives no use figure, purchases do", () => {
    expect(deal(3, "2026-02-01").impacts[0].reason).toMatch(/not a purchase/);
    expect(Number(deal(3, "2026-07-01").impacts[0].value)).toBeCloseTo(0.25);
  });
});

describe("consideration bridge", () => {
  it("draws a term that is not stated as an open segment, never zero", () => {
    const parts = bridgeParts(deal(1, "2026-03-10").bridge, 720);
    expect(parts.map((p) => [p.kind, p.state])).toEqual([["cash", "stated"], ["stock", "stated"], ["debt", "not_stated"], ["earnout", "not_stated"]]);
    expect(parts.filter((p) => p.state === "not_stated").every((p) => p.w > 0 && p.amount === null)).toBe(true);
    expect(parts.reduce((a, p) => a + p.w, 0)).toBeCloseTo(720);
    expect(deal(1, "2026-03-10").bridge.total_is_lower_bound).toBe(true);
  });
});

describe("waterfalls", () => {
  it("draws what has its inputs and names why the rest is not drawn", () => {
    const { shown, notDrawn } = impactWaterfalls(deal(1, "2026-03-10", 40));
    expect(shown.map((w) => w.id)).toEqual(["premium-announced", "premium-owner_scenario", "dilution-announced"]);
    expect(shown[1].steps.every((s) => s.derived)).toBe(true); // dashed: your scenario
    expect(shown[2].steps.map((s) => s.value)).toEqual([1_000_000_000, 100_000_000, 1_100_000_000]);
    expect(notDrawn[0].reason).toMatch(/no debt amount is stated/);
  });
});

describe("timeline", () => {
  it("places marks by effective date, ticks by known date, and flags terminal and contested marks", () => {
    const t = timelineLayout(deal(1, "2026-09-30"), 860);
    expect(t.marks.map((m) => m.state)).toEqual(["announced", "definitive", "contested", "approved", "closed"]);
    const xs = t.marks.map((m) => m.x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(t.marks.filter((m) => m.terminal).map((m) => m.state)).toEqual(["contested", "closed"]);
    expect(t.marks[3].xKnown).toBeGreaterThan(t.marks[3].x);
    expect(t.versions.map((v) => v.versionNo)).toEqual([1, 1, 2]);
    expect(t.ticks.length).toBeGreaterThan(0);
    expect(timelineLayout({ transitions: [], term_versions: [] }, 860).marks).toEqual([]);
  });
});

describe("filters and portfolio", () => {
  it("filters by type and state", () => {
    const all = sampleDeals(ms("2026-09-30")).deals;
    expect(filterDeals(all, { type: "buyback", state: "" }).map((d) => d.id)).toEqual([3]);
    expect(filterDeals(all, { type: "", state: "terminated" }).map((d) => d.id)).toEqual([4]);
    expect(filterDeals(all, { type: "", state: "" })).toHaveLength(all.length);
    // A size filter never lets a deal with no stated size through, and a currency filter is exact.
    const sized = filterDeals(all, { type: "", state: "", minSize: 1 });
    expect(sized.every((d) => d.size != null && Number(d.size) >= 1)).toBe(true);
    expect(filterDeals(all, { type: "", state: "", currency: "ZZZ" })).toEqual([]);
  });

  it("shows the denominator, covered weight and holdings with none known", () => {
    const p = portfolioExposure({ A: 0.5, B: 0.3, C: 0.2 }, [{ ticker: "A", dealId: 1, title: "x", dealType: "acquisition", state: "announced" }, { ticker: "B", dealId: 2, title: "y", dealType: "acquisition", state: "closed" }], "2026-10-01T00:00:00Z");
    expect(Number(p.denominator)).toBeCloseTo(1);
    expect(Number(p.covered_weight)).toBeCloseTo(0.5);
    expect(p.none_known).toEqual(["C"]);
    const s = samplePortfolioDeals(ms("2026-09-30"));
    expect(s.none_known).toEqual(["DUNE", "GLEN"]);
  });
});
