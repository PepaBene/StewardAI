// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

vi.mock("@/lib/holding-data/evidence-items", () => ({
  EVIDENCE_DOMAINS: [{ id: "filed-notes", label: "Filed notes" }],
  evidenceItems: () => [
    {
      key: "k1",
      title: "dei:CurrentFiscalYearEndDate",
      detail: "period 2026-06-27",
      passage: null,
      passageReason: "none",
      knownAt: "2026-07-31T14:01:02Z",
      coverageReason: null,
      sourceUrl: "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm",
      source: "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627-with-a-very-long-unbreakable-label.htm",
    },
  ],
}));
vi.mock("@/lib/holding-data/logic", () => ({
  evidenceCounts: () => [{ domain: "filed-notes", label: "Filed notes", items: 1, state: "verified", reason: "1 note." }],
}));

import type { EvidenceSummary } from "@/lib/holding-data/types";

import { EvidenceLedger } from "./evidence-ledger";

describe("EvidenceLedger", () => {
  it("lets a long unbroken source label wrap instead of widening the page (live defect: 917px page at 390px)", () => {
    setup(<EvidenceLedger summary={{ known_as_of: null, dependencies: { excluded_unconfirmed: 0 } } as unknown as EvidenceSummary} />);
    const row = screen.getByText("dei:CurrentFiscalYearEndDate").closest("li");
    expect(row?.className).toContain("wrap-anywhere");
  });
});
