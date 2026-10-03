// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import type { ControlStakesOut } from "@/lib/xray/types";

import { ControlStakesBody } from "./ownership-section";

afterEach(() => cleanup());

const stake = {
  accession: "0000950123-26-001111",
  form: "SC 13G",
  schedule: "13G" as const,
  is_amendment: false,
  amendment_no: null,
  filed: "2026-08-12",
  event_date: "2026-07-31",
  issuer_name: "Apple Inc",
  issuer_cusip: "037833100",
  class_title: "Common Stock",
  stake_shares: 1_000_000,
  stake_percent: "6.5",
  stake_basis: "reported",
  intent_text: null,
  prior_accession: null,
  holders: [{ person_name: "Example Capital", person_cik: null, member_of_group: false, sole_voting: null, shared_voting: null, sole_dispositive: null, shared_dispositive: null, aggregate_shares: 1_000_000, percent_of_class: "6.5", person_type: "IA" }],
  reconciliation: { match_basis: "cusip" as const, class_status: "cusip_match" as const, class_label: "Common", class_shares_outstanding: 15_000_000_000, class_shares_as_of: "2026-06-30", computed_percent: "0.007", percent_check: "not_checkable" as const, note: null },
  changes: [],
  source_url: "https://www.sec.gov/Archives/x",
};
const out = (over: Partial<ControlStakesOut>): ControlStakesOut => ({ ticker: "AAPL_US_EQ", channel: "beneficial_ownership_13dg", stakes: [], superseded: [], limitations: [], ...over });

describe("13D/G stakes view", () => {
  it("shows a parsed stake with its filer, percent and issuer CUSIP", () => {
    render(<ControlStakesBody data={out({ stakes: [stake] })} />);
    expect(screen.getByText("Example Capital")).toBeTruthy();
    expect(screen.getByText("6.50%")).toBeTruthy();
    expect(screen.getByText("CUSIP 037833100")).toBeTruthy();
  });
  it("says CUSIP is not stated rather than leaving it blank", () => {
    render(<ControlStakesBody data={out({ stakes: [{ ...stake, issuer_cusip: null }] })} />);
    expect(screen.getByText("CUSIP not stated")).toBeTruthy();
  });
  it("points to the job log for skipped or unreadable filings, with and without stored stakes (never silently missing)", () => {
    const { unmount } = render(<ControlStakesBody data={out({})} />);
    expect(screen.getByText(/not that no one holds a large stake/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the job log" }).getAttribute("href")).toBe("/activity#job-log");
    unmount();
    render(<ControlStakesBody data={out({ stakes: [stake] })} />);
    expect(screen.getByText(/could not read, or that is older than structured filings/)).toBeTruthy();
  });
});
