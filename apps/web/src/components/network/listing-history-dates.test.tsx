// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

const base = {
  mic: "OCXE",
  publication_date: "2026-09-27",
  status: "UNCH",
  terminated_at: null,
  issuer_lei: null,
  currency: "USD",
  source_url: "https://registers.esma.europa.eu/x",
  known_at: "2026-10-03T16:57:26.890654Z",
};

vi.mock("@/lib/network/client", () => ({
  fetchListingHistory: () =>
    Promise.resolve({
      data: [
        { ...base, published_at: "2026-09-27T05:00:00Z", valid_from: "2020-01-02", valid_to: "2024-06-30", first_trade_at: "2020-01-02T08:00:00Z" },
        { ...base, published_at: "2026-09-27T05:10:00Z", valid_from: "2024-07-01", valid_to: null, first_trade_at: null },
        { ...base },
      ],
      response: new Response("{}", { status: 200 }),
    }),
  fetchLegalEntityHistory: () => Promise.resolve({ data: [], response: new Response("{}", { status: 200 }) }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { ListingHistory } from "./identity-panel";

afterEach(() => vi.restoreAllMocks());

describe("ListingHistory date fields", () => {
  it("shows published_at, valid_from, valid_to and first_trade_at so same-venue rows are distinguishable", async () => {
    setup(<ListingHistory isin="US0378331005" asOfParam={undefined} />);
    const lists = await screen.findAllByRole("list");
    const rows = within(lists[0]).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const texts = rows.map((r) => r.textContent);
    expect(new Set(texts).size).toBe(3);
    expect(rows[0].textContent).toContain("2020-01-02");
    expect(rows[0].textContent).toContain("2024-06-30");
    expect(rows[0].textContent).toContain("2026-09-27 05:00:00 UTC");
    expect(rows[0].textContent).toContain("2020-01-02 08:00:00 UTC");
    expect(rows[1].textContent).toContain("2024-07-01");
    expect(rows[1].textContent).toContain("open or not reported");
  });
  it("says 'not reported' for a field the register left out, never a date", async () => {
    setup(<ListingHistory isin="US0378331005" asOfParam={undefined} />);
    const lists = await screen.findAllByRole("list");
    const last = within(lists[0]).getAllByRole("listitem")[2];
    expect(last.textContent).toContain("Register published atnot reported");
    expect(last.textContent).toContain("First tradenot reported");
  });
});
