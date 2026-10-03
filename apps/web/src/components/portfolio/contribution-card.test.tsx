// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/portfolio/contribution-waterfall", () => ({ ContributionWaterfall: () => <div data-testid="waterfall" /> }));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import type { ContributionReport } from "@/lib/api/client";

import { ContributionContent } from "./contribution-card";

afterEach(() => cleanup());

const row = { ticker: "AAPL_US_EQ", name: "Apple", contribution_base: 0, contribution_pct: 0 };

describe("ContributionContent", () => {
  it("does not draw a chart of zeros when the period has no span (live defect: one snapshot, every holding 0.0, gap null)", () => {
    const data: ContributionReport = { as_of: "2026-10-03", period_start: "2026-10-03", base_currency: "CZK", holdings: [row], gap: null };
    render(<ContributionContent data={data} />);
    expect(screen.queryByTestId("waterfall")).toBeNull();
    expect(screen.getByText(/Only one snapshot exists/)).toBeTruthy();
  });
  it("draws the chart when the period spans at least two snapshots", () => {
    const data: ContributionReport = { as_of: "2026-10-03", period_start: "2026-09-01", base_currency: "CZK", holdings: [{ ...row, contribution_base: 12 }], gap: null };
    render(<ContributionContent data={data} />);
    expect(screen.getByTestId("waterfall")).toBeTruthy();
  });
});
