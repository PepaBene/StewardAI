// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

const row = (status: string) => ({
  mic: "OCXE",
  publication_date: "2026-09-27",
  status,
  terminated_at: null,
  issuer_lei: null,
  currency: "USD",
  source_url: "https://registers.esma.europa.eu/x",
  known_at: "2026-10-03T16:57:26.890654Z",
});

vi.mock("@/lib/network/client", () => ({
  fetchListingHistory: () => Promise.resolve({ data: [row("MODF"), row("UNCH"), row("UNCH")], response: new Response("{}", { status: 200 }) }),
  fetchLegalEntityHistory: () => Promise.resolve({ data: [], response: new Response("{}", { status: 200 }) }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { ListingHistory } from "./identity-panel";

afterEach(() => vi.restoreAllMocks());

describe("ListingHistory", () => {
  it("lists every record ESMA publishes for one venue and date, with unique keys (live defect: OCXE has four)", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    setup(<ListingHistory isin="US0378331005" asOfParam={undefined} />);
    expect(await screen.findAllByText(/OCXE/)).toHaveLength(3);
    expect(errors.mock.calls.filter((c) => String(c[0]).includes("same key"))).toEqual([]);
  });
});
