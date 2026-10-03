// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setup } from "@/test/dom";

let job: Record<string, unknown>;
vi.mock("@/lib/api/client", () => ({
  getBrowserApiClient: () => ({ GET: () => Promise.resolve({ data: job, response: new Response("{}", { status: 200 }) }) }),
}));
vi.mock("@/components/sync/sync-status-provider", () => ({ useSyncStatus: () => ({ lastSuccessJobId: null }) }));

import { JobView } from "./job-view";

const base = {
  id: 41,
  job_type: "insider_trades",
  trigger: "schedule",
  status: "succeeded",
  error: null,
  created_at: "2026-10-03T10:00:00Z",
  started_at: "2026-10-03T10:00:01Z",
  finished_at: "2026-10-03T10:10:01Z",
  params: {},
  attempts: 1,
};
const step = (over: Record<string, unknown>) => ({ step_index: 0, name: "import", status: "succeeded", started_at: "2026-10-03T10:00:02Z", finished_at: "2026-10-03T10:09:02Z", error: null, detail: null, model: null, cost_usd: null, ...over });

describe("job view: deferred and interrupted runs", () => {
  it("says deferred filings are waiting for the next run and are not an error (insider trades)", async () => {
    job = {
      ...base,
      steps: [step({ detail: { filings_imported: 400, deferred_by_issuer: { "0000320193": 120, "0000789019": 3 }, deferred_reason: "run budget (600 s) reached; the next run continues" } })],
    };
    setup(<JobView id={41} />);
    expect(await screen.findByText(/Deferred to the next run: 123 filings/)).toBeTruthy();
    expect(screen.getByText(/Not an error and nothing is lost/)).toBeTruthy();
    const list = screen.getByRole("list", { name: "Deferred filings by issuer" });
    expect(within(list).getByText(/Issuer CIK 0000320193: 120 filings waiting/)).toBeTruthy();
    expect(screen.queryByText("Failed")).toBeNull();
  });
  it("shows an interrupted run as Interrupted with plain wording, not as a bare failure (regression: it read 'Failed')", async () => {
    job = {
      ...base,
      status: "failed",
      error: "interrupted (the process that started it stopped; run 42 began)",
      finished_at: null,
      steps: [step({ status: "failed", error: "interrupted: the worker stopped during this step" })],
    };
    setup(<JobView id={41} />);
    expect(await screen.findByText("The run was interrupted")).toBeTruthy();
    expect(screen.getByText(/It is not a data error/)).toBeTruthy();
    expect(screen.getAllByText("Interrupted").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("Failed")).toBeNull();
  });
  it("still shows a real failure as Failed", async () => {
    job = { ...base, status: "failed", error: "ValueError: boom", steps: [step({ status: "failed", error: "ValueError: boom" })] };
    setup(<JobView id={41} />);
    expect((await screen.findAllByText("Failed")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Interrupted")).toBeNull();
  });
  it("lists each unreadable 13D/G filing with its named cause and URL, and the skip counts (never silently missing)", async () => {
    job = {
      ...base,
      job_type: "control_stakes",
      steps: [
        step({
          name: "store-filings",
          detail: {
            candidates: 6,
            stored: 2,
            parse_failed: 2,
            unstructured_skipped: 1,
            not_held_skipped: 1,
            parse_failures: [
              { accession: "0000950123-26-001111", form: "SC 13D", reason: "document is HTML, not the submission XML [https://www.sec.gov/Archives/edgar/data/1/000095012326001111/primary_doc.xml]" },
              { accession: "0000950123-26-002222", form: "SC 13G/A", reason: "no reporting person found" },
            ],
          },
        }),
      ],
    };
    setup(<JobView id={41} />);
    expect(await screen.findByText("2 filings could not be read")).toBeTruthy();
    const list = screen.getByRole("list", { name: "Filings that could not be read, with the cause" });
    expect(within(list).getByText("document is HTML, not the submission XML")).toBeTruthy();
    const link = within(list).getByRole("link");
    expect(link.getAttribute("href")).toBe("https://www.sec.gov/Archives/edgar/data/1/000095012326001111/primary_doc.xml");
    expect(within(list).getByText("no reporting person found")).toBeTruthy();
    expect(within(list).getAllByRole("link")).toHaveLength(1);
    const skipped = screen.getByRole("list", { name: "Skipped filings" });
    expect(within(skipped).getByText(/1 older filings with no structured data/)).toBeTruthy();
    expect(within(skipped).getByText(/2 filings that could not be read/)).toBeTruthy();
  });
});
