// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }), usePathname: () => "/", useSearchParams: () => new URLSearchParams() }));

import type { ContributionReport } from "@/lib/api/client";

import { Movers } from "./digest-view";

afterEach(() => cleanup());

describe("digest Movers with fewer than two snapshots", () => {
  it("shows the API gap and the needs-two-snapshots state, never rows", () => {
    const gap = "only one position snapshot (2026-10-03) in this window; a contribution needs at least two snapshots to measure a change";
    const data: ContributionReport = { as_of: "2026-10-03", period_start: "2026-10-03", base_currency: "CZK", holdings: [], gap };
    render(<Movers movers={data} />);
    expect(screen.getByText("Needs at least two snapshots")).toBeTruthy();
    expect(screen.getByText(gap)).toBeTruthy();
    expect(screen.queryByLabelText("Contribution by holding")).toBeNull();
  });
  it("old wire (one snapshot, zero rows, no gap) is not read as 'no holding moved'", () => {
    const data: ContributionReport = {
      as_of: "2026-10-03",
      period_start: "2026-10-03",
      base_currency: "CZK",
      holdings: [{ ticker: "AAPL_US_EQ", name: "Apple", contribution_base: 0, contribution_pct: 0 }],
      gap: null,
    };
    render(<Movers movers={data} />);
    expect(screen.getByText("Needs at least two snapshots")).toBeTruthy();
    expect(screen.queryByText(/No holding moved/)).toBeNull();
  });
});
