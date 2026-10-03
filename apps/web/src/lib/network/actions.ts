import { getBrowserApiClient } from "@/lib/api/client";
import { fieldErrors, flattenErrors } from "@/lib/api/validation-errors";

import type { NetworkEdge } from "./types";

/*
 * Owner writes of the business-network view. Each one is an explicit decision: nothing here
 * is called without a button press, and the quota-spending provider refreshes are only
 * reachable through the confirm step in components/network/refresh-actions.tsx.
 */

export type ActionResult = { ok: true; message: string } | { ok: false; message: string };

type Raw = { data?: unknown; error?: unknown; response: Response };

function describe(r: Raw): string {
  const errors = fieldErrors(r.error);
  const flat = flattenErrors(errors).map((e) => (e.path ? `${e.path}: ${e.message}` : e.message));
  const detail = flat.length ? flat.join("; ") : "";
  const hint = r.response.status === 409 ? " (refused by a transition or configuration rule)" : r.response.status === 429 ? " (free quota exhausted)" : "";
  return `HTTP ${r.response.status}${hint}${detail ? `: ${detail}` : ""}`;
}

async function run(call: () => Promise<Raw>, okMessage: (data: unknown) => string): Promise<ActionResult> {
  try {
    const r = await call();
    if (r.response.ok) return { ok: true, message: okMessage(r.data) };
    return { ok: false, message: describe(r) };
  } catch (err) {
    return { ok: false, message: `Backend unreachable: ${err instanceof Error ? err.message : String(err)}` };
  }
}

export type Decision = "confirm" | "reject" | "dispute" | "reopen";

export const MIN_REASON = 10;

export function reasonProblem(reason: string): string | null {
  return reason.trim().length >= MIN_REASON ? null : `Give a reason of at least ${MIN_REASON} characters.`;
}

/** Edge decision. GLEIF/Wikidata rows (legacy ids) only support confirm/reject and take no reason. */
export function decideEdge(edge: NetworkEdge, decision: Decision, reason: string): Promise<ActionResult> {
  if (edge.rowId === null) return Promise.resolve({ ok: false, message: "This edge has no decidable id." });
  const id = edge.rowId;
  if (edge.legacy) {
    if (decision !== "confirm" && decision !== "reject") {
      return Promise.resolve({ ok: false, message: "A GLEIF or Wikidata relationship can only be confirmed or rejected." });
    }
    const path = decision === "confirm" ? "/relationships/{relationship_id}/confirm" : "/relationships/{relationship_id}/reject";
    return run(
      () => getBrowserApiClient().POST(path, { params: { path: { relationship_id: id } } }),
      () => `Relationship ${decision === "confirm" ? "confirmed" : "rejected"}.`,
    );
  }
  return run(
    () => getBrowserApiClient().POST("/company-graph/edges/{edge_id}/decision", { params: { path: { edge_id: id } }, body: { decision, reason: reason.trim() } }),
    (d) => `Edge ${statusOf(d) ?? decision}.`,
  );
}

function statusOf(d: unknown): string | null {
  return d && typeof d === "object" && "status" in d && typeof d.status === "string" ? d.status : null;
}

export const decideEvent = (ticker: string, eventId: number, decision: Decision, reason: string) =>
  run(
    () =>
      getBrowserApiClient().POST("/holdings/{ticker}/company-events/{event_id}/decision", {
        params: { path: { ticker, event_id: eventId } },
        body: { decision, reason: reason.trim() },
      }),
    (d) => `Event ${statusOf(d) ?? decision}.`,
  );

export const decideDisclosure = (ticker: string, disclosureId: number, decision: Decision, reason: string) =>
  run(
    () =>
      getBrowserApiClient().POST("/holdings/{ticker}/relationship-disclosures/{disclosure_id}/decision", {
        params: { path: { ticker, disclosure_id: disclosureId } },
        body: { decision, reason: reason.trim() },
      }),
    (d) => `Disclosure ${statusOf(d) ?? decision}.`,
  );

export const reviewFilingHit = (hitId: number) =>
  run(
    () => getBrowserApiClient().POST("/relationship-review/hits/{hit_id}/review", { params: { path: { hit_id: hitId } } }),
    () => "Filing read; passages that name the holding are stored as suggested disclosures.",
  );

export const resolveContradiction = (id: number, resolution: "resolved" | "dismissed", note: string) =>
  run(
    () =>
      getBrowserApiClient().POST("/relationship-review/contradictions/{contradiction_id}/resolve", {
        params: { path: { contradiction_id: id } },
        body: { resolution, note: note.trim() },
      }),
    () => `Contradiction ${resolution}.`,
  );

/* Owner-triggered provider refreshes: each spends free-tier quota. */

export const refreshDiffbot = (ticker: string) =>
  run(
    () => getBrowserApiClient().POST("/holdings/{ticker}/company-research/diffbot", { params: { path: { ticker } } }),
    (d) => `Diffbot refresh done${countOf(d, "suggested_edges", "suggested edge")}.`,
  );

export const refreshPdl = (ticker: string) =>
  run(
    () => getBrowserApiClient().POST("/holdings/{ticker}/company-research/pdl", { params: { path: { ticker } } }),
    (d) => {
      const skipped = d && typeof d === "object" && "skipped" in d && typeof d.skipped === "string" ? d.skipped : null;
      return skipped ? `PDL not called: ${skipped}.` : `PDL refresh done${countOf(d, "matched_records", "matched record")}.`;
    },
  );

export const refreshEpo = (ticker: string) =>
  run(
    () => getBrowserApiClient().POST("/holdings/{ticker}/patent-families/epo-ops/refresh", { params: { path: { ticker } } }),
    (d) => `EPO OPS refresh done${countOf(d, "patent_candidates", "patent candidate")}.`,
  );

function countOf(d: unknown, key: string, noun: string): string {
  if (!d || typeof d !== "object" || !(key in d)) return "";
  const n = (d as Record<string, unknown>)[key];
  return typeof n === "number" ? `: ${n} ${noun}${n === 1 ? "" : "s"}` : "";
}

/* Suggestion forms. Status is fixed to "suggested": a confirmation is a separate decision with a reason. */

export type EdgeSuggestion = {
  subjectKey: string;
  objectKey: string;
  objectName: string;
  edgeType: "partner" | "acquirer" | "acquired" | "investor" | "investment" | "product" | "technology" | "executive" | "customer" | "supplier" | "competitor";
  direction: "outgoing" | "incoming" | "symmetric";
  source: string;
  sourceUrl: string;
  validFrom: string;
  validTo: string;
  passage: string;
};

export const suggestEdge = (s: EdgeSuggestion) =>
  run(
    () =>
      getBrowserApiClient().POST("/company-graph/edges", {
        body: {
          subject_key: s.subjectKey,
          object_key: s.objectKey.trim() || s.objectName.trim(),
          object_name: s.objectName.trim(),
          edge_type: s.edgeType,
          direction: s.direction,
          source: s.source.trim(),
          source_url: s.sourceUrl.trim(),
          status: "suggested",
          valid_from: s.validFrom || null,
          valid_to: s.validTo || null,
          passage: s.passage.trim() || null,
        },
      }),
    (d) => `Edge recorded as ${statusOf(d) ?? "suggested"}.`,
  );

export type DisclosureReview = {
  relationship: "customer" | "supplier" | "procurement_award";
  direction: "issuer_sells_to" | "issuer_buys_from" | "award_to_issuer" | "unknown";
  counterpartyName: string;
  anonymous: boolean;
  /** Percent as typed (0-100); converted to a fraction for the API. */
  sharePercent: string;
  shareBasis: string;
  fiscalPeriod: string;
  validFrom: string;
  passage: string;
  source: string;
  sourceUrl: string;
};

/** Percent text -> fraction in [0,1], or null when blank; undefined when it is not a valid percent. */
export function percentToFraction(text: string): number | null | undefined {
  const t = text.trim().replace(/%$/, "");
  if (t === "") return null;
  const n = Number(t);
  if (!Number.isFinite(n) || n < 0 || n > 100) return undefined;
  return n / 100;
}

export const recordDisclosureReview = (ticker: string, d: DisclosureReview) => {
  const share = percentToFraction(d.sharePercent);
  if (share === undefined) return Promise.resolve<ActionResult>({ ok: false, message: "The reported share must be a percentage between 0 and 100." });
  return run(
    () =>
      getBrowserApiClient().POST("/holdings/{ticker}/relationship-disclosures", {
        params: { path: { ticker } },
        body: {
          relationship: d.relationship,
          direction: d.direction,
          counterparty_name: d.anonymous ? null : d.counterpartyName.trim() || null,
          anonymous_counterparty: d.anonymous,
          reported_share: share,
          share_basis: d.shareBasis || null,
          fiscal_period: d.fiscalPeriod.trim() || null,
          valid_from: d.validFrom || null,
          passage: d.passage.trim(),
          source: d.source.trim(),
          source_url: d.sourceUrl.trim(),
          status: "suggested",
        },
      }),
    () => "Passage recorded as a suggested disclosure.",
  );
};

export type EventSuggestion = {
  eventType: "deal" | "financing";
  eventState: "proposed" | "announced" | "amended" | "approved" | "closed" | "terminated" | "reversed";
  counterparty: string;
  date: string;
  source: string;
  sourceUrl: string;
};

export const recordDealEvent = (ticker: string, e: EventSuggestion) =>
  run(
    () =>
      getBrowserApiClient().POST("/holdings/{ticker}/company-events", {
        params: { path: { ticker } },
        body: {
          event_type: e.eventType,
          event_state: e.eventState,
          announced_at: e.eventState === "announced" || e.eventState === "proposed" ? e.date || null : null,
          effective_at: e.eventState === "announced" || e.eventState === "proposed" ? null : e.date || null,
          source: e.source.trim(),
          source_url: e.sourceUrl.trim(),
          counterparty_key: e.counterparty.trim() || null,
          status: "suggested",
        },
      }),
    () => "Deal state recorded as suggested.",
  );
