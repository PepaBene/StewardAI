import type { EvidenceAnswer, Replay } from "@/lib/disclosure/replay";

/** Sample replay for 2026-03-10: the 10-K/A and the revised consensus are known only afterwards. */
export const REPLAY_FIXTURE: Replay = {
  as_of: "2026-03-10T23:59:59Z",
  read_at: "2026-10-07T00:00:00Z",
  snapshot_date: "2026-03-09",
  notes: ["Holdings are from the last snapshot, 2026-03-09; later trades are not reflected."],
  holdings: [
    {
      ticker: "ACME",
      quantity: "120",
      value: "21600",
      currency: "USD",
      issuer_id: 1,
      datasets: {
        filings: {
          known: [{ label: "10-K 0001-26-000010", known_at: "2026-02-20T21:00:00Z", detail: "a later version replaced it, period FY2025", ref: "11" }],
          since: [{ label: "10-K/A 0001-26-000044", known_at: "2026-04-02T12:00:00Z", detail: "current version, period FY2025", ref: "12" }],
          leaked_dropped: 0,
          error: null,
        },
        estimates: {
          known: [{ label: "published analyst consensus: revenue FY2026", known_at: "2026-03-01T00:00:00Z", detail: "100 amount", ref: "21" }],
          since: [{ label: "published analyst consensus: revenue FY2026", known_at: "2026-05-01T00:00:00Z", detail: "90 amount", ref: "22" }],
          leaked_dropped: 0,
          error: null,
        },
        deals: { known: [], since: [], leaked_dropped: 0, error: "Could not read deals: PostgresError." },
        ownership: { known: [], since: [], leaked_dropped: 0, error: null },
        insiders: { known: [], since: [], leaked_dropped: 0, error: null },
      },
    },
  ],
};

export const ANSWER_FIXTURE: EvidenceAnswer = {
  ticker: "ACME",
  as_of: "2026-03-10T23:59:59Z",
  text: "For ACME as of 2026-03-10: relationship_customer with an undisclosed counterparty, from SEC 10-K, known 2026-02-20.",
  grounded: true,
  phrased_by: "template",
  limits: ["Only verified facts known by the date are used; machine suggestions are not."],
  evidence: [
    {
      kind: "claim",
      id: 1,
      label: "relationship_customer",
      counterparty: "Customer A",
      value: null,
      period_end: null,
      quote: "Customer A accounted for 12% of revenue",
      section: "Concentration",
      known_at: "2026-02-20T21:00:00Z",
      source: "SEC 10-K",
      document_url: null,
      anonymous: true,
    },
  ],
  retrieved: 14,
  note: null,
};
