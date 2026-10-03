// @vitest-environment jsdom
/*
 * Keyboard focus in the arm-then-confirm owner actions. Arming swaps the trigger button for a
 * Confirm button (and back): the focused element unmounts, and without explicit handling focus
 * falls to <body> and a keyboard user loses their place. Each test drives the control with the
 * keyboard only and asserts where document.activeElement is. These need a DOM, so they run in
 * jsdom (the rest of the suite runs in node).
 */
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { active, setup } from "@/test/dom";

const post = vi.fn();
vi.mock("@/lib/api/client", () => ({
  getBrowserApiClient: () => ({ POST: post }),
}));
vi.mock("@/lib/network/actions", () => ({
  refreshDiffbot: vi.fn(async () => ({ ok: true, message: "done" })),
  refreshPdl: vi.fn(async () => ({ ok: true, message: "done" })),
  refreshEpo: vi.fn(async () => ({ ok: false, message: "refused" })),
  reviewFilingHit: vi.fn(async () => ({ ok: true, message: "read" })),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { PortfolioCoveragePanel } from "@/components/markets/portfolio-coverage";
import { ProbePanel } from "@/components/financials/probe-panel";
import { HitRow } from "@/components/network/review-queue";
import { RefreshActions } from "@/components/network/refresh-actions";
import { ResetBlock } from "@/components/sources/coverage-matrix";
import type { HoldingsPrices } from "@/lib/markets/types";
import type { FilingHit } from "@/lib/network/types";
import type { AccessRow } from "@/lib/source-coverage/types";

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ data: { id: 7, status: "queued" }, response: new Response("{}", { status: 200 }) });
});

const button = (name: string | RegExp) => screen.getByRole("button", { name });

describe("PortfolioCoveragePanel: refresh price history", () => {
  const data = {
    days: 90,
    holdings: [{ ticker: "AAPL_US_EQ", bars: [{ date: "2026-10-02", close: 1 }], source: "yahoo", as_of: "2026-10-02", last_close: 1, day_change_pct: 0, quote_currency: "USD", quote_currency_basis: "reported" }],
  } as unknown as HoldingsPrices;

  it("moves focus to the confirm button, back to the trigger on Cancel, and again after the job is queued", async () => {
    const { user } = setup(<PortfolioCoveragePanel data={data} />);
    const trigger = button("Refresh price history…");
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Yes, run job"));
    expect(post).not.toHaveBeenCalled();
    await user.tab();
    expect(active()).toBe(button("Cancel"));
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Refresh price history…"));
    expect(post).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}"); // Confirm
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(active()).toBe(button("Refresh price history…")));
  });
});

describe("RefreshActions: spend free quota", () => {
  it("arms a provider, moves focus to Confirm, returns it to that provider's trigger and never runs on arming", async () => {
    const { refreshDiffbot } = await import("@/lib/network/actions");
    const { user } = setup(<RefreshActions ticker="AAPL_US_EQ" onDone={() => {}} />);
    const triggers = screen.getAllByRole("button", { name: "Refresh…" });
    triggers[1].focus(); // the second provider
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Confirm: spend free quota"));
    expect(refreshDiffbot).not.toHaveBeenCalled();
    await user.keyboard("{Tab}{Enter}"); // Cancel
    expect(active()).toBe(screen.getAllByRole("button", { name: "Refresh…" })[1]);
    await user.keyboard("{Enter}{Enter}"); // arm again, then Confirm
    await waitFor(() => expect(active()).toBe(screen.getAllByRole("button", { name: "Refresh…" })[1]));
    expect(await screen.findByRole("status")).toBeTruthy();
  });

  it("keeps focus on the trigger after a refused call, and shows the refusal", async () => {
    const { user } = setup(<RefreshActions ticker="AAPL_US_EQ" onDone={() => {}} />);
    const triggers = screen.getAllByRole("button", { name: "Refresh…" });
    triggers[2].focus(); // EPO: refused by the mock
    await user.keyboard("{Enter}{Enter}");
    expect(await screen.findByRole("alert")).toBeTruthy();
    await waitFor(() => expect(active()).toBe(screen.getAllByRole("button", { name: "Refresh…" })[2]));
  });
});

describe("ProbePanel: one free request per confirmation", () => {
  it("focuses Confirm when armed and returns to Run on Cancel without calling the API", async () => {
    const { user } = setup(<ProbePanel ticker="AAPL_US_EQ" onDone={() => {}} />);
    const run = screen.getAllByRole("button", { name: "Run…" })[0];
    run.focus();
    await user.keyboard("{Enter}");
    expect(active()?.textContent).toBe("Confirm");
    await user.keyboard("{Tab}{Enter}");
    expect(active()).toBe(screen.getAllByRole("button", { name: "Run…" })[0]);
    expect(post).not.toHaveBeenCalled();
  });

  it("returns focus to Run once the probe has finished", async () => {
    post.mockResolvedValue({ data: { status: "ok" }, response: new Response("{}", { status: 200 }) });
    const { user } = setup(<ProbePanel ticker="AAPL_US_EQ" onDone={() => {}} />);
    screen.getAllByRole("button", { name: "Run…" })[0].focus();
    await user.keyboard("{Enter}{Enter}");
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(active()).toBe(screen.getAllByRole("button", { name: "Run…" })[0]));
  });
});

describe("HitRow: read a filing", () => {
  const hit: FilingHit = { id: 1, ticker: "AAPL_US_EQ", filerName: "Filer", form: "10-K", filedAt: "2026-01-01T00:00:00Z", url: null, reviewState: "pending", reviewNote: null, attempts: 0, disclosureId: null };
  it("moves focus into the confirmation and back", async () => {
    const { reviewFilingHit } = await import("@/lib/network/actions");
    const { user } = setup(<ul><HitRow hit={hit} onChanged={() => {}} /></ul>);
    button("Read passages…").focus();
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Confirm: read filing"));
    expect(reviewFilingHit).not.toHaveBeenCalled();
    await user.keyboard("{Tab}{Enter}");
    expect(active()).toBe(button("Read passages…"));
    await user.keyboard("{Enter}{Enter}");
    await waitFor(() => expect(reviewFilingHit).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(active()).toBe(button("Read passages…")));
  });
});

describe("ResetBlock: lift a provider's hard block", () => {
  const row = { id: "x", provider: "finnhub", endpoint: "etf_holdings", hardBlocked: true, rawState: "paid_only" } as unknown as AccessRow;

  it("puts focus on the safe choice (Cancel) when armed and back on the trigger when cancelled", async () => {
    const onReset = vi.fn(async () => null);
    const { user } = setup(<ResetBlock row={row} onReset={onReset} />);
    button("Reset block…").focus();
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Cancel"));
    await user.keyboard("{Enter}");
    expect(active()).toBe(button("Reset block…"));
    expect(onReset).not.toHaveBeenCalled();
  });

  it("does not leave focus on <body> after the block is lifted", async () => {
    const onReset = vi.fn(async () => null);
    const { user } = setup(<ResetBlock row={row} onReset={onReset} />);
    button("Reset block…").focus();
    await user.keyboard("{Enter}{Shift>}{Tab}{/Shift}{Enter}"); // Cancel -> Lift block
    await waitFor(() => expect(onReset).toHaveBeenCalledTimes(1));
    await screen.findByText(/Block lifted/);
    expect(active()).not.toBe(document.body);
  });
});
