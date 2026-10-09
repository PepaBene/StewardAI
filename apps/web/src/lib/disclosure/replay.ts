import type { components } from "@/lib/api/schema";

/*
 * Decision replay (plan P8.7) and the evidence answer path (P8.6). `known` is what could have been seen
 * on the chosen date; `since` is what became known afterwards and is always shown apart, so a later
 * filing or revised value cannot pass as hindsight-free. An answer is built from verified stored
 * evidence only and lists what it could not find.
 */

type S = components["schemas"];
export type Replay = S["ReplayOut"];
export type ReplayHolding = S["ReplayHoldingOut"];
export type ReplayDataset = S["ReplayDatasetOut"];
export type ReplayItem = S["ReplayItemOut"];
export type EvidenceAnswer = S["AnswerOut"];

export const DATASET_LABEL: Record<string, string> = {
  filings: "Filings",
  estimates: "Published estimates",
  deals: "Deals",
  ownership: "Owners",
  insiders: "Insider trades",
};

export const datasetLabel = (key: string): string => DATASET_LABEL[key] ?? key.replaceAll("_", " ");

/** End of the chosen calendar day in UTC: everything published that day counts as known. */
export const asOfFromDay = (day: string): string | null => (/^\d{4}-\d{2}-\d{2}$/.test(day) && !Number.isNaN(Date.parse(`${day}T23:59:59Z`)) ? `${day}T23:59:59Z` : null);

export type DatasetCounts = { key: string; known: number; since: number; error: string | null };

/** Per dataset: how many items were known then, how many appeared later, and any read failure. */
export function datasetCounts(h: ReplayHolding): DatasetCounts[] {
  return Object.entries(h.datasets).map(([key, d]) => ({ key, known: d.known.length, since: d.since.length, error: d.error }));
}

/** Whole-portfolio totals; an unreadable dataset is counted, not treated as empty. */
export function replayTotals(r: Replay): { known: number; since: number; unreadable: number } {
  let known = 0;
  let since = 0;
  let unreadable = 0;
  for (const h of r.holdings)
    for (const d of Object.values(h.datasets)) {
      known += d.known.length;
      since += d.since.length;
      if (d.error) unreadable += 1;
    }
  return { known, since, unreadable };
}

/** Keeps the known/since split visible in tests: nothing in `known` may be stamped after `asOf`. */
export const leaksFuture = (d: ReplayDataset, asOf: string): boolean => d.known.some((i) => i.known_at != null && Date.parse(i.known_at) > Date.parse(asOf));

export const answerState = (a: EvidenceAnswer): "grounded" | "no_evidence" | "withheld" =>
  !a.grounded || a.text == null ? "withheld" : a.evidence.length === 0 ? "no_evidence" : "grounded";
