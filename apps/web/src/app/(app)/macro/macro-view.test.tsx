// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

const ok = (data: unknown) => Promise.resolve({ data, response: new Response("{}", { status: 200 }) });

vi.mock("@/lib/macro/api", () => ({
  fetchHoldings: () => ok({ holdings: [{ ticker: "SPY_US_EQ", name: "SPDR" }] }),
  fetchPortfolioCountryContext: () => ok({}),
  fetchPortfolioMacroCoverage: () => ok({}),
  fetchExposureMappings: () => ok([]),
  fetchMacroContext: () => ok({ effects: [] }),
  runMacroCollect: () => ok({ id: 1, status: "queued" }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));
// The children are not under test: only the keys MacroView gives its siblings are.
vi.mock("@/components/macro/context-cards", () => ({ ContextCards: () => <p>context</p> }));
vi.mock("@/components/macro/trade-panel", () => ({ TradePanel: () => <p>trade</p> }));
vi.mock("@/components/macro/exposure-editor", () => ({ ExposureEditor: () => <p>editor</p> }));
vi.mock("@/components/macro/holding-macro-panel", () => ({ HoldingMacroPanel: () => <p>panel</p> }));
vi.mock("@/components/macro/portfolio-exposure-map", () => ({ PortfolioExposureMap: () => <p>map</p> }));

import { MacroView } from "./macro-view";

afterEach(() => vi.restoreAllMocks());

describe("MacroView", () => {
  it("gives its sibling panels distinct keys (live defect: ContextCards and TradePanel both keyed by the ticker)", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    setup(<MacroView />);
    expect(await screen.findByText("context")).toBeTruthy();
    expect(screen.getByText("trade")).toBeTruthy();
    const duplicate = errors.mock.calls.filter((c) => String(c[0]).includes("two children with the same key"));
    expect(duplicate).toEqual([]);
  });
});
