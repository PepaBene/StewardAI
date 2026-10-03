// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fetchIssuerNames = vi.fn();
vi.mock("@/lib/macro/api", () => ({
  createExposure: vi.fn(),
  deleteExposure: vi.fn(),
  fetchIssuerName: vi.fn(async () => ({ data: { issuerId: 0, names: [] }, response: new Response(null, { status: 200 }) })),
  fetchIssuerNames: (ids: number[]) => fetchIssuerNames(ids),
}));

import { ExposureEditor } from "./exposure-editor";
import type { ExposureMapping } from "@/lib/macro/types";

afterEach(() => {
  cleanup();
  fetchIssuerNames.mockReset();
});

const mapping = (id: number, issuerId: number, issuerName: string | null): ExposureMapping => ({
  id,
  issuerId,
  issuerName,
  segment: "Cloud",
  geography: "DE",
  hsRevision: null,
  hsCode: null,
  basis: "reported",
  weight: 0.4,
  validFrom: "2020-01-01",
  validTo: null,
});

describe("exposure editor issuer labels", () => {
  it("labels another holding's issuer with its legal name, not 'Issuer N' (regression: old UI said 'Issuer 7')", async () => {
    fetchIssuerNames.mockResolvedValue({
      data: [{ id: 7, name: "ASML Holding N.V.", lei: null, cik: null, country: "NL", tickers: ["ASML"] }],
      response: new Response(null, { status: 200 }),
    });
    render(<ExposureEditor mappings={[mapping(1, 3, "SAP SE"), mapping(2, 7, null)]} issuers={new Map([[3, "SAP"]])} defaultIssuerId={3} ticker="SAP" onChanged={() => {}} />);
    await waitFor(() => expect(screen.getAllByText(/ASML Holding N\.V\. \(issuer 7\)/).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/SAP SE \(SAP, issuer 3\)/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/^Issuer 7$/)).toBeNull();
    expect(fetchIssuerNames).toHaveBeenCalledWith([3, 7]);
  });
  it("uses the name already on the mapping row while the lookup is still pending", () => {
    fetchIssuerNames.mockReturnValue(new Promise(() => {}));
    render(<ExposureEditor mappings={[mapping(2, 7, "ASML Holding N.V.")]} issuers={new Map()} defaultIssuerId={null} onChanged={() => {}} />);
    expect(screen.getAllByText(/ASML Holding N\.V\. \(issuer 7\)/).length).toBeGreaterThan(0);
  });
  it("falls back to an explicit unknown label when the backend has no name, never an invented one", async () => {
    fetchIssuerNames.mockResolvedValue({ data: [], response: new Response(null, { status: 200 }) });
    render(<ExposureEditor mappings={[mapping(2, 9, null)]} issuers={new Map()} defaultIssuerId={null} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getAllByText(/Issuer 9 \(name unknown\)/).length).toBeGreaterThan(0));
  });
});
