import { describe, expect, it } from "vitest";

import { managerBook, SAMPLE_STAKES, sampleOwnership } from "./fixtures-ownership";
import { endOfDayMs } from "./model";
import { laneBar, lagLabel, rankFlowGraph, stakeDeltas, stakesAt } from "./ownership";

const BEFORE = endOfDayMs("2025-01-01");
const FEB = endOfDayMs("2026-03-01");
const MAY = endOfDayMs("2026-05-01");
const LATE = endOfDayMs("2026-10-01");

const lane = (cut: number, key: string) => sampleOwnership(cut).lanes.find((l) => l.key === key)!;

describe("ownership lanes at a cutoff", () => {
  it("shows nothing and no denominator before anything was filed", () => {
    const v = sampleOwnership(BEFORE);
    expect(v.denominator).toBeNull();
    expect(v.lanes.filter((l) => l.status === "covered")).toHaveLength(0);
    expect(v.warnings.some((w) => w.includes("No denominator"))).toBe(true);
  });

  it("totals each lane only within itself and offers no combined total", () => {
    const v = sampleOwnership(FEB);
    expect(Number(lane(FEB, "institutional_13f").total_shares)).toBe(1_250_000 + 900_000 + 520_000 + 610_000);
    expect(Number(lane(FEB, "fund_nport").total_shares)).toBe(180_000);
    expect("combined_total" in v).toBe(false);
    expect(v.warnings.some((w) => w.includes("overlap; not added"))).toBe(true);
  });

  it("a restatement replaces a manager's report only once it was known", () => {
    const beta = (cut: number) => lane(cut, "institutional_13f").entries.find((e) => e.holder === "Beta Partners")!;
    expect(beta(FEB).shares).toBe("900000");
    expect(beta(FEB).amended).toBe(false);
    expect(beta(MAY).shares).toBe("700000");
    expect(beta(MAY).amended).toBe(true);
  });

  it("states the remainder against the denominator and the lag of each figure", () => {
    const l = lane(FEB, "institutional_13f");
    expect(Number(l.remainder_shares)).toBe(10_000_000 - Number(l.total_shares));
    expect(l.entries[0].lag_days).toBe(41); // 31 Dec to 10 Feb
    expect(lane(FEB, "fund_nport").entries[0].lag_days).toBe(59); // fund report 31 Oct to filing 29 Dec
  });

  it("marks a national lane as not covered rather than empty", () => {
    expect(lane(LATE, "national_voting").status).toBe("not_covered");
  });

  it("draws a bar from the percent of denominator and flags overflow", () => {
    expect(laneBar(lane(FEB, "institutional_13f"))?.over).toBe(false);
    expect(laneBar(lane(BEFORE, "institutional_13f"))).toBeNull();
    const over = { ...lane(FEB, "institutional_13f"), percent_of_denominator: "120" };
    expect(laneBar(over)).toEqual({ fill: 100, over: true });
  });
});

describe("rank flow", () => {
  const prev = managerBook("2025-09-30", MAY);
  const cur = managerBook("2025-12-31", MAY);
  const g = rankFlowGraph(prev, cur, 6);
  const move = (m: string) => g.moves.find((x) => x.manager === m)!;

  it("describes new, exited and changed holders", () => {
    expect(move("Delta Funds").kind).toBe("exited");
    expect(move("Epsilon Management").kind).toBe("new");
    expect(move("Alpha Capital").kind).toBe("increased");
    expect(move("Beta Partners").kind).toBe("decreased"); // 800k to the restated 700k
  });

  it("joins holders present in both quarters and routes the rest outside the list", () => {
    const alpha = g.links.find((l) => l.source === "p:Alpha Capital")!;
    expect(alpha.target).toBe("c:Alpha Capital");
    expect(alpha.value).toBe(1_100_000); // the smaller of the two figures
    expect(g.links.find((l) => l.source === "p:Delta Funds")?.target).toBe("c:out");
    expect(g.links.find((l) => l.target === "c:Epsilon Management")?.source).toBe("p:out");
    expect(g.nodes.some((n) => n.id === "c:out")).toBe(true);
  });

  it("uses the original figure before the restatement was known", () => {
    expect(managerBook("2025-12-31", FEB)["Beta Partners"]).toBe(900_000);
    expect(managerBook("2025-12-31", MAY)["Beta Partners"]).toBe(700_000);
  });
});

describe("control stakes", () => {
  it("lists only filings known by the cutoff, oldest first", () => {
    expect(stakesAt(SAMPLE_STAKES, BEFORE)).toEqual([]);
    expect(stakesAt(SAMPLE_STAKES, FEB).map((s) => s.form)).toEqual(["SC 13G", "SC 13G/A"]);
    expect(stakesAt(SAMPLE_STAKES, LATE).map((s) => s.form).at(-1)).toBe("SC 13D/A");
  });

  it("reports a delta only between two reported percentages", () => {
    const d = stakeDeltas(stakesAt(SAMPLE_STAKES, LATE));
    expect(d.map((x) => Math.round((x.delta ?? 0) * 10) / 10)).toEqual([1.6, 2.3, 1.4]);
    const gap = stakeDeltas([
      { holder: "x", form: "SC 13G", percent: null, known_at: "2026-01-01T00:00:00Z", event_date: null, amendment: null },
      { holder: "x", form: "SC 13G/A", percent: 6, known_at: "2026-02-01T00:00:00Z", event_date: null, amendment: 1 },
    ]);
    expect(gap[0].delta).toBeNull();
  });

  it("the stake lane is a floor and says so", () => {
    expect(lane(LATE, "control_13dg").notes.some((n) => n.includes("floor"))).toBe(true);
  });
});

describe("lag label", () => {
  it("never leaves the lag blank", () => {
    expect(lagLabel(null)).toBe("Lag unknown");
    expect(lagLabel(1)).toBe("1 day after");
    expect(lagLabel(41)).toBe("41 days after");
  });
});
