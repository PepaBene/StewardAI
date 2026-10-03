// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

let strip: unknown;
vi.mock("@/lib/api/client", () => ({
  getBrowserApiClient: () => ({ GET: () => Promise.resolve({ data: strip, response: new Response("{}", { status: 200 }) }) }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { MarketStrip } from "./market-strip";

afterEach(() => cleanup());

const px = { symbol: "^px", label: "PX", yahoo_symbol: null, last_close: 1812.4, as_of: "2026-10-03", as_of_time: null, exchange_tz: null, kind: "close", source: "Prague Stock Exchange", previous_close: null, previous_date: null, change_pct: null };

describe("MarketStrip with a one-day PX history", () => {
  it("shows the level, says there is no change yet, and names the source (regression: the index vanished from the strip)", async () => {
    strip = { base_currency: "CZK", fx: [], indices: [px] };
    render(<MarketStrip />);
    const item = await screen.findByText(/first close, no change yet/);
    expect(item.closest("li")?.textContent).toContain("PX");
    expect(item.closest("li")?.textContent).toContain("1,812.40");
    const li = item.closest("li")!;
    expect(li.getAttribute("title")).toContain("Prague Stock Exchange (pse)");
    expect(li.getAttribute("title")).toContain("nothing earlier is backfilled");
    expect(li.textContent).not.toMatch(/[+−]\d/);
  });
  it("shows a real change once a second close exists, and still hides an index with no value", async () => {
    strip = { base_currency: "CZK", fx: [], indices: [{ ...px, previous_close: 1800, previous_date: "2026-10-02", change_pct: 0.0069 }, { ...px, symbol: "^none", label: "NONE", last_close: null, as_of: null, change_pct: null, source: null }] };
    render(<MarketStrip />);
    expect(await screen.findByText("+0.69%")).toBeTruthy();
    expect(screen.queryByText(/NONE/)).toBeNull();
  });
});
