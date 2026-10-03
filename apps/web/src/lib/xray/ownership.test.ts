import { describe, expect, it } from "vitest";

import { breadthRows, buildRankFlow, filingLagDays, nportState, ownershipProvenance, quarterLabel, stakeViews } from "./ownership";
import type { ControlStake, HoldingOwnership, OwnershipPeriod, TopHolder } from "./types";

function period(p: string, over: Partial<OwnershipPeriod> = {}): OwnershipPeriod {
  return { period: p, holders: 10, shares: 1000, value_usd: 5000, latest_filed: null, holders_change: null, shares_change: null, new_holders: null, exited_holders: null, ...over };
}
function holder(cik: string, shares: number, prev: number | null, over: Partial<TopHolder> = {}): TopHolder {
  return { cik, manager_name: `M${cik}`, shares, value_usd: shares * 2, accession: `0000000000-26-00000${cik}`, prev_shares: prev, ...over };
}
function own(over: Partial<HoldingOwnership> = {}): HoldingOwnership {
  return { ticker: "AAPL_US_EQ", cusips: [], periods: [], top_holders: [], source: "SEC Form 13F data sets", applicable: true, ...over };
}

describe("quarterLabel / filingLagDays", () => {
  it("labels calendar quarters", () => {
    expect(quarterLabel("2026-06-30")).toBe("Q2 2026");
    expect(quarterLabel("2025-12-31")).toBe("Q4 2025");
    expect(quarterLabel("nonsense")).toBe("nonsense");
  });
  it("computes the lag and rejects missing or inverted dates", () => {
    expect(filingLagDays("2026-06-30", "2026-08-14")).toBe(45);
    expect(filingLagDays("2026-06-30", null)).toBeNull();
    expect(filingLagDays("2026-06-30", "2026-06-01")).toBeNull();
  });
});

describe("breadthRows", () => {
  it("orders chronologically, keeps the oldest change as null and flags a missing quarter", () => {
    const rows = breadthRows([
      period("2026-06-30", { holders_change: 5, shares_change: "200", latest_filed: "2026-08-10" }),
      period("2025-12-31", { holders_change: null }),
    ]);
    expect(rows.map((r) => r.quarter)).toEqual(["Q4 2025", "Q2 2026"]);
    expect(rows[0].holdersChange).toBeNull();
    expect(rows[1].holdersChange).toBe(5);
    expect(rows[1].sharesChange).toBe(200);
    expect(rows[1].quartersSincePrevious).toBe(2);
    expect(rows[1].lagDays).toBe(41);
  });
});

describe("buildRankFlow", () => {
  it("ranks by shares now and before, with ties broken by CIK and new holders unranked before", () => {
    const flow = buildRankFlow(
      own({
        periods: [period("2026-06-30", { latest_filed: "2026-08-14" }), period("2026-03-31", { latest_filed: "2026-05-15" })],
        top_holders_previous_period: "2026-03-31",
        top_holders: [holder("1", 500, 300), holder("2", 400, 450), holder("3", 400, null), holder("4", 100, 100)],
      }),
    )!;
    expect(flow.rows.map((r) => [r.cik, r.rankNow, r.rankBefore])).toEqual([
      ["1", 1, 2],
      ["2", 2, 1],
      ["3", 3, null],
      ["4", 4, 3],
    ]);
    expect(flow.rows[0].move).toBe(1);
    expect(flow.rows[2].move).toBeNull();
    expect(flow.latestLagDays).toBe(45);
    expect(flow.previousLagDays).toBe(45);
    expect(flow.droppedNotShown).toBe(true);
  });
  it("ignores holders without a positive share count and is null when none are left", () => {
    expect(buildRankFlow(own({ top_holders: [holder("1", 0, null)] }))).toBeNull();
  });
});

describe("ownershipProvenance", () => {
  const now = new Date("2026-10-03T00:00:00Z");
  it("is not_covered for non-applicable listings", () => {
    expect(ownershipProvenance(own({ applicable: false, not_applicable_reason: "non-US" }), now)).toMatchObject({ state: "not_covered", reason: "non-US" });
  });
  it("is unavailable without a stored quarter, stale when old, verified when recent", () => {
    expect(ownershipProvenance(own(), now).state).toBe("unavailable");
    expect(ownershipProvenance(own({ periods: [period("2026-03-31")] }), now).state).toBe("stale");
    const ok = ownershipProvenance(own({ periods: [period("2026-06-30", { latest_filed: "2026-08-12" })] }), now);
    expect(ok.state).toBe("verified");
    expect(ok.asOf).toBe("2026-06-30");
    expect(ok.knownAt).toBe("2026-08-12");
  });
});

describe("nportState", () => {
  it("never reads no_positions as zero ownership", () => {
    const s = nportState({ state: "no_positions", funds_loaded: 2, funds_holding: 0, latest_report_period_end: null, oldest_report_period_end: null, scope: "" });
    expect(s.state).toBe("not_covered");
    expect(s.reason).toContain("not evidence");
  });
});

describe("stakeViews", () => {
  const base = {
    accession: "a", form: "SC 13D", schedule: "13D", is_amendment: false, amendment_no: null, filed: "2026-05-01", event_date: null, issuer_name: "X", issuer_cusip: null, class_title: "Common", stake_shares: "1000", stake_percent: "7.5", stake_basis: "reported", intent_text: null, prior_accession: null, holders: [], changes: [], source_url: "u",
    reconciliation: { match_basis: "cusip", class_status: "cusip_match", class_label: null, class_shares_outstanding: null, class_shares_as_of: null, computed_percent: null, percent_check: "not_checkable", note: null },
  } as unknown as ControlStake;
  it("keeps the filed percent per stake (never summed) and flags intent changes and groups", () => {
    const views = stakeViews([
      base,
      { ...base, accession: "b", filed: "2026-06-01", stake_percent: null, changes: [{ change_type: "intent_changed", person_name: null, old_value: null, new_value: null, detail: null }], holders: [{ person_name: "A", member_of_group: true }, { person_name: "B", member_of_group: true }] } as unknown as ControlStake,
    ]);
    expect(views.map((v) => v.stake.accession)).toEqual(["b", "a"]);
    expect(views[0].percent).toBeNull();
    expect(views[0].intentChanged).toBe(true);
    expect(views[0].isGroup).toBe(true);
    expect(views[1].percent).toBe(7.5);
    expect(views[1].checkLabel).toContain("not checkable");
  });
});
