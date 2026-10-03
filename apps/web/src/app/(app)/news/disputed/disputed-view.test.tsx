// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

const source = (url: string, tier = 1) => ({ url, tier, headline: `Headline ${url}`, published_at: "2026-10-01T09:00:00Z", outlet: "Outlet", news_item_id: null });
const story = (id: number) => ({
  id,
  ticker: "AAPL_US_EQ",
  headline: `Disputed story ${id}`,
  event_type: "earnings",
  severity: 3,
  first_seen: "2026-10-01T08:00:00Z",
  last_seen: "2026-10-02T08:00:00Z",
  status_reason: "contradicted by a tier-1 source",
  note: null,
  resolution_note: null,
  verdicts_frozen: false,
  resolved_at: null,
  resolution: null,
  claim: [source(`https://a.example/${id}`, 3)],
  contradicting: [source(`https://b.example/${id}`)],
  supporting: [source(`https://c.example/${id}`)],
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {} }),
  usePathname: () => "/news/disputed",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig<object>()),
  getBrowserApiClient: () => ({ GET: () => Promise.resolve({ data: [story(1), story(2), story(3)], response: new Response("{}", { status: 200 }) }), POST: vi.fn() }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { DisputedView } from "./disputed-view";

describe("disputed stories page landmarks", () => {
  it("names every claim, contradiction and support region after its story (axe landmark-unique: they all said 'The claim')", async () => {
    setup(<DisputedView />);
    await screen.findByText("Disputed story 1");
    const labels = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    // Three stories, each with the claim, contradicting and supporting regions.
    expect(labels).toHaveLength(9);
    expect(new Set(labels).size).toBe(9);
    for (const id of [1, 2, 3]) {
      expect(labels).toContain(`The claim: story ${id}`);
      expect(labels).toContain(`Contradicted by: story ${id}`);
      expect(labels).toContain(`Supported by: story ${id}`);
    }
  });
  it("gives every story card its own accessible name", async () => {
    setup(<DisputedView />);
    await screen.findByText("Disputed story 1");
    const names = screen.getAllByRole("listitem").filter((li) => li.hasAttribute("aria-labelledby")).map((li) => document.getElementById(li.getAttribute("aria-labelledby")!)?.textContent);
    expect(names).toEqual(["Disputed story 1", "Disputed story 2", "Disputed story 3"]);
  });
});
