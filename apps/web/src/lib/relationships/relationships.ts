import type { components } from "@/lib/api/schema";
import { isRecord, num, recs, str, titleCase, type Rec } from "@/lib/parse-untyped";
import { URL_RE } from "@/lib/reference/form-utils";

/* Parsers, validation and request mapping for the Relationship review page. The review queue,
 * reconciliations, the global graph lists and decision history are typed; the per-holding
 * business-network and corporate-action lists are still read defensively: missing stays null. */

export const TICKER_RE = /^[A-Za-z0-9._-]{1,32}$/;
export const DECISION_REASON_MIN = 10;
export const DECISION_REASON_MAX = 2000;
export const RESOLUTION_NOTE_MIN = 5;
export const RESOLUTION_NOTE_MAX = 1000;

export type GraphStatus = "confirmed" | "suggested" | "disputed" | "rejected";
export type GraphDecision = "confirm" | "reject" | "dispute" | "reopen";

export const DECISION_RESULT: Record<GraphDecision, GraphStatus> = { confirm: "confirmed", reject: "rejected", dispute: "disputed", reopen: "suggested" };

export const DECISION_LABEL: Record<GraphDecision, string> = { confirm: "Confirm", reject: "Reject", dispute: "Dispute", reopen: "Send back to review" };

const STATUS_WORD: Record<GraphStatus, string> = { confirmed: "Confirmed", suggested: "Suggested", disputed: "Disputed", rejected: "Rejected" };
export const statusWord = (s: GraphStatus): string => STATUS_WORD[s];

export function toStatus(v: unknown): GraphStatus | null {
  return v === "confirmed" || v === "suggested" || v === "disputed" || v === "rejected" ? v : null;
}

/** Decisions that make sense from a status. A decided row can only be moved by an explicit decision. */
export function decisionsFor(status: GraphStatus | null): GraphDecision[] {
  if (status === "confirmed") return ["reject", "dispute"];
  if (status === "rejected" || status === "disputed") return ["reopen"];
  return ["confirm", "reject", "dispute"];
}

export const normTicker = (t: string): string => t.trim();
export function validateTicker(t: string): string | undefined {
  return TICKER_RE.test(normTicker(t)) ? undefined : "Use 1 to 32 letters, digits, dots, dashes or underscores.";
}

/** The reason is required for every decision and is read after trimming, which is stricter than the API. */
export function validateReason(reason: string, min = DECISION_REASON_MIN, max = DECISION_REASON_MAX): string | undefined {
  const n = reason.trim().length;
  if (n < min) return `Give a reason of at least ${min} characters (now ${n}).`;
  if (reason.length > max) return `Keep the reason within ${max} characters.`;
  return undefined;
}

export const decisionBody = (decision: GraphDecision, reason: string) => ({ decision, reason: reason.trim() });
export const resolutionBody = (resolution: "resolved" | "dismissed", note: string) => ({ resolution, note: note.trim() });

/* ---- parsers ---- */

export type Disclosure = {
  id: number;
  ticker: string | null;
  relationship: string;
  direction: string;
  counterparty: string;
  anonymous: boolean;
  share: number | null;
  shareBasis: string | null;
  fiscalPeriod: string | null;
  passage: string | null;
  sourceUrl: string | null;
  source: string | null;
  status: GraphStatus | null;
  decisionReason: string | null;
  knownAt: string | null;
  origin: string | null;
};

export function parseDisclosure(r: Rec, fallbackTicker?: string): Disclosure | null {
  const id = num(r.id);
  if (id === null) return null;
  const anonymous = r.anonymous_counterparty === true;
  return {
    id,
    ticker: str(r.ticker) ?? fallbackTicker ?? null,
    relationship: str(r.relationship) ?? "unknown",
    direction: str(r.direction) ?? "unknown",
    counterparty: anonymous ? "Not named by the filer" : (str(r.counterparty_name) ?? str(r.counterparty_key) ?? "Unnamed"),
    anonymous,
    share: num(r.reported_share),
    shareBasis: str(r.share_basis),
    fiscalPeriod: str(r.fiscal_period),
    passage: str(r.passage),
    sourceUrl: str(r.source_url),
    source: str(r.source),
    status: toStatus(r.status),
    decisionReason: str(r.decision_reason),
    knownAt: str(r.known_at),
    origin: str(r.origin),
  };
}

export type FilingHit = { id: number; ticker: string; filer: string | null; form: string | null; filedAt: string | null; url: string | null; state: string; note: string | null; attempts: number | null; status: string };
export type Contradiction = { status: string; id: number; ticker: string; relationship: string; fiscalPeriod: string | null; kind: string; disclosureId: number | null; otherId: number | null; detail: string | null; createdAt: string | null };
export type ReviewQueue = { hits: FilingHit[]; disclosures: Disclosure[]; contradictions: Contradiction[] };

/** The typed queue (GET /relationship-review/queue). Each row carries its own `status`, so a disclosure is "suggested" or "disputed" by the API, not by guesswork. */
export function parseQueue(raw: components["schemas"]["ReviewQueueResponse"]): ReviewQueue {
  const hits: FilingHit[] = raw.filing_hits.map((r) => ({ id: r.id, ticker: r.ticker, filer: r.filer_name ?? null, form: r.form ?? null, filedAt: r.filed_at ?? null, url: r.url ?? null, state: r.review_state || "pending", note: r.review_note ?? null, attempts: r.attempts ?? null, status: r.status }));
  const disclosures = raw.suggested_disclosures.flatMap((r) => parseDisclosure(r) ?? []);
  const contradictions: Contradiction[] = raw.contradictions.map((r) => {
    const d = r.detail; // `detail` is free-form on the wire
    return {
      id: r.id,
      ticker: r.ticker,
      relationship: r.relationship ?? "unknown",
      fiscalPeriod: r.fiscal_period ?? null,
      kind: r.kind || "unknown",
      disclosureId: r.disclosure_id ?? null,
      otherId: r.other_disclosure_id ?? null,
      detail: typeof d === "string" ? d : isRecord(d) ? Object.entries(d).map(([k, v]) => `${titleCase(k)}: ${String(v)}`).join("; ") : null,
      createdAt: r.created_at || null,
      status: r.status,
    };
  });
  return { hits, disclosures, contradictions };
}

/* ---- global lists and decision history ---- */

export type GraphKind = "edges" | "events" | "disclosures";
export const GRAPH_KINDS: readonly { id: GraphKind; label: string }[] = [
  { id: "edges", label: "Edges" },
  { id: "events", label: "Events" },
  { id: "disclosures", label: "Disclosures" },
];
export const STATUS_FILTERS: readonly GraphStatus[] = ["suggested", "confirmed", "disputed", "rejected"];
export const PAGE_SIZE = 25;

export type GraphListRow = {
  id: number;
  ticker: string;
  kind: string;
  direction: string | null;
  counterparty: string | null;
  status: GraphStatus | null;
  /** The raw word when it is none of the four known statuses; shown as is, never as "confirmed". */
  rawStatus: string;
  tier: string | null;
  source: string;
  sourceUrl: string | null;
  passage: string | null;
  knownAt: string;
  decidedAt: string | null;
  decisionReason: string | null;
  validFrom: string | null;
  validTo: string | null;
  anonymous: boolean;
  /** Fraction 0 to 1 as reported by the filer; null stays "not reported". */
  reportedShare: number | null;
  fiscalPeriod: string | null;
};
export type GraphListPage = { total: number; limit: number; offset: number; rows: GraphListRow[] };

export function parseGraphPage(p: components["schemas"]["GraphRowsPage"]): GraphListPage {
  return {
    total: p.total,
    limit: p.limit,
    offset: p.offset,
    rows: p.rows.map((r) => ({
      id: r.id,
      ticker: r.ticker,
      kind: r.kind,
      direction: r.direction ?? null,
      counterparty: r.anonymous_counterparty ? "Not named by the filer" : (r.counterparty ?? r.object_key ?? null),
      status: toStatus(r.status),
      rawStatus: r.status,
      tier: r.evidence_tier ?? null,
      source: r.source,
      sourceUrl: r.source_url || null,
      passage: r.passage ?? null,
      knownAt: r.known_at,
      decidedAt: r.decided_at ?? null,
      decisionReason: r.decision_reason ?? null,
      validFrom: r.valid_from ?? null,
      validTo: r.valid_to ?? null,
      anonymous: r.anonymous_counterparty === true,
      reportedShare: r.reported_share ?? null,
      fiscalPeriod: r.fiscal_period ?? null,
    })),
  };
}

export const pageRange = (p: GraphListPage): string => (p.total === 0 ? "No rows" : `${p.offset + 1} to ${Math.min(p.offset + p.rows.length, p.total)} of ${p.total}`);

export type HistoryEntry = { at: string; from: GraphStatus | null; to: GraphStatus | null; toWord: string; reason: string | null; by: string | null };
export type History = { kind: "edge" | "event" | "disclosure" | "suggestion"; id: number; current: GraphStatus | null; currentWord: string; entries: HistoryEntry[]; limitations: string[] };

/** Newest first. A status the owner never set (a first record) has no `from`. */
export function parseHistory(h: components["schemas"]["DecisionHistory"]): History {
  const entries = h.entries
    .map((e) => ({ at: e.changed_at, from: toStatus(e.from_status), to: toStatus(e.to_status), toWord: e.to_status, reason: e.reason ?? null, by: e.decided_by ?? null }))
    .sort((a, b) => b.at.localeCompare(a.at));
  return { kind: h.kind, id: h.id, current: toStatus(h.current_status), currentWord: h.current_status, entries, limitations: h.limitations };
}

export type ReconcileResolution = "resolved" | "dismissed";
export const RECONCILE_LABEL: Record<ReconcileResolution, string> = { resolved: "Resolve: the change is explained", dismissed: "Dismiss: not a real change" };
/** POST /company-graph/reconciliations/{id}/resolve; the reason is the owner's, trimmed. */
export const reconcileBody = (resolution: ReconcileResolution, reason: string) => ({ resolution, reason: reason.trim() });
export const reconcileConfirmText = (r: { id: number; ticker: string }, resolution: ReconcileResolution): string =>
  `Record ${resolution === "resolved" ? "this change as resolved" : "this change as dismissed"} for ${r.ticker} (change #${r.id})? Your reason is stored with it.`;

export type Edge = { id: number; objectName: string; objectKey: string | null; edgeType: string; direction: string; status: GraphStatus | null; validFrom: string | null; validTo: string | null; tier: string | null; source: string | null; sourceUrl: string | null; passage: string | null; decisionReason: string | null; knownAt: string | null };
export type CompanyEvent = { id: number; eventType: string; eventState: string; announcedAt: string | null; effectiveAt: string | null; status: GraphStatus | null; source: string | null; sourceUrl: string | null; counterparty: string | null; evidenceId: number | null };
export type Network = { edges: Edge[]; events: CompanyEvent[]; disclosures: Disclosure[] };

export function parseNetwork(raw: unknown, ticker: string): Network {
  const o = isRecord(raw) ? raw : {};
  const edges: Edge[] = [];
  for (const r of recs(o.edges)) {
    const id = num(r.id);
    if (id === null) continue;
    edges.push({ id, objectName: str(r.object_name) ?? str(r.object_key) ?? "Unnamed", objectKey: str(r.object_key), edgeType: str(r.edge_type) ?? "unknown", direction: str(r.direction) ?? "unknown", status: toStatus(r.status), validFrom: str(r.valid_from), validTo: str(r.valid_to), tier: str(r.evidence_tier), source: str(r.source), sourceUrl: str(r.source_url), passage: str(r.passage), decisionReason: str(r.decision_reason), knownAt: str(r.known_at) });
  }
  const events: CompanyEvent[] = [];
  for (const r of recs(o.events)) {
    const id = num(r.id);
    if (id === null) continue;
    events.push({ id, eventType: str(r.event_type) ?? "unknown", eventState: str(r.event_state) ?? "unknown", announcedAt: str(r.announced_at), effectiveAt: str(r.effective_at), status: toStatus(r.status), source: str(r.source), sourceUrl: str(r.source_url), counterparty: str(r.counterparty_key), evidenceId: num(r.evidence_id) });
  }
  const disclosures = recs(o.disclosures).flatMap((r) => parseDisclosure(r, ticker) ?? []);
  return { edges, events, disclosures };
}

export type Reconciliation = { id: number; ticker: string; changeKind: string; fromKey: string | null; toKey: string | null; source: string | null; observedAt: string | null; outcome: string };

export function parseReconciliations(raw: components["schemas"]["ReconciliationsResponse"]): Reconciliation[] {
  return raw.open.map((r) => ({ id: r.id, ticker: r.ticker, changeKind: r.change_kind || "change", fromKey: r.from_key ?? null, toKey: r.to_key ?? null, source: r.observed_source || null, observedAt: r.observed_at || null, outcome: r.outcome || "unexplained" }));
}

export type CorporateAction = { id: number; symbol: string; actionType: string; exDate: string | null; ratio: number | null; cashAmount: number | null; currency: string | null; source: string | null; sourceUrl: string | null; status: GraphStatus | null; evidenceId: number | null; decisionReason: string | null };

export function parseActions(raw: unknown): CorporateAction[] {
  const out: CorporateAction[] = [];
  for (const r of recs(raw)) {
    const id = num(r.id);
    if (id === null) continue;
    out.push({ id, symbol: str(r.symbol) ?? "", actionType: str(r.action_type) ?? "unknown", exDate: str(r.ex_date), ratio: num(r.ratio), cashAmount: num(r.cash_amount), currency: str(r.currency), source: str(r.source), sourceUrl: str(r.source_url), status: toStatus(r.status), evidenceId: num(r.evidence_id), decisionReason: str(r.decision_reason) });
  }
  return out;
}

/** Share of a disclosure is a 0 to 1 fraction as reported by the filer; shown as reported, never summed. */
export const shareText = (s: number | null): string => (s === null ? "not reported" : `${(s * 100).toFixed(1).replace(/\.0$/, "")}% as reported`);

/** Only http(s) addresses become links. */
export const safeUrl = (u: string | null): string | null => (u && URL_RE.test(u) ? u : null);

/* ---- corporate actions ---- */

export const ACTION_TYPES = ["split", "cash_dividend", "stock_dividend", "symbol_change", "merger", "spin_off", "delisting"] as const;
export type ActionType = (typeof ACTION_TYPES)[number];
export const CURRENCY_RE = /^([A-Z]{3}|GBp|ZAc|ILa)$/;
export const SYMBOL_RE = /^[A-Za-z0-9.^_-]{1,40}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export type ActionDraft = {
  actionType: ActionType;
  symbol: string;
  exDate: string;
  recordDate: string;
  paymentDate: string;
  ratio: string;
  cashAmount: string;
  currency: string;
  per: "listed_line" | "ordinary_share";
  amountBasis: "as_declared" | "split_adjusted";
  source: string;
  sourceUrl: string;
  evidenceId: string;
  status: "suggested" | "confirmed" | "rejected";
};

export const emptyActionDraft = (symbol = ""): ActionDraft => ({ actionType: "cash_dividend", symbol, exDate: "", recordDate: "", paymentDate: "", ratio: "", cashAmount: "", currency: "", per: "listed_line", amountBasis: "as_declared", source: "", sourceUrl: "", evidenceId: "", status: "suggested" });

const parseNum = (t: string): number | null | "invalid" => {
  const s = t.trim().replace(",", ".");
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : "invalid";
};
const parseId = (t: string): number | null | "invalid" => {
  const s = t.trim();
  if (s === "") return null;
  return /^\d+$/.test(s) && Number(s) > 0 ? Number(s) : "invalid";
};

/** Mirrors CorporateActionInput, including its cross-field rules. */
export function validateAction(d: ActionDraft): Partial<Record<keyof ActionDraft, string>> {
  const e: Partial<Record<keyof ActionDraft, string>> = {};
  if (!SYMBOL_RE.test(d.symbol.trim())) e.symbol = "Use 1 to 40 letters, digits, dots, carets, dashes or underscores.";
  if (!DATE_RE.test(d.exDate.trim())) e.exDate = "Enter the ex-date as YYYY-MM-DD.";
  if (d.recordDate.trim() && !DATE_RE.test(d.recordDate.trim())) e.recordDate = "Use YYYY-MM-DD or leave blank.";
  if (d.paymentDate.trim() && !DATE_RE.test(d.paymentDate.trim())) e.paymentDate = "Use YYYY-MM-DD or leave blank.";
  const ratio = parseNum(d.ratio);
  if (ratio === "invalid" || (ratio !== null && ratio <= 0)) e.ratio = "The ratio must be a number above 0.";
  else if ((d.actionType === "split" || d.actionType === "stock_dividend") && ratio === null) e.ratio = d.actionType === "split" ? "A split needs its ratio." : "A stock dividend needs its ratio; a cash amount cannot stand in for it.";
  const cash = parseNum(d.cashAmount);
  if (cash === "invalid" || (cash !== null && cash < 0)) e.cashAmount = "The amount must be 0 or more.";
  else if (d.actionType === "cash_dividend" && cash === null) e.cashAmount = "A cash dividend needs its amount.";
  if (d.currency.trim() && !CURRENCY_RE.test(d.currency.trim())) e.currency = "Use a 3-letter code such as USD, or GBp, ZAc or ILa for minor units.";
  else if (d.actionType === "cash_dividend" && !d.currency.trim()) e.currency = "A cash dividend needs its currency.";
  const s = d.source.trim().length;
  if (s < 2 || s > 80) e.source = "Name the source in 2 to 80 characters.";
  const u = d.sourceUrl.trim();
  if (u.length < 8 || u.length > 2000 || !URL_RE.test(u)) e.sourceUrl = "Enter the full web address of the source (8 to 2000 characters).";
  const ev = parseId(d.evidenceId);
  if (ev === "invalid") e.evidenceId = "Evidence is a whole number above 0.";
  else if (d.status === "confirmed" && ev === null) e.evidenceId = "A confirmed action needs linked source evidence.";
  return e;
}

export function actionRequest(d: ActionDraft) {
  const ratio = parseNum(d.ratio);
  const cash = parseNum(d.cashAmount);
  const ev = parseId(d.evidenceId);
  const opt = (t: string) => (t.trim() === "" ? null : t.trim());
  return {
    action_type: d.actionType,
    symbol: d.symbol.trim(),
    ex_date: d.exDate.trim(),
    record_date: opt(d.recordDate),
    payment_date: opt(d.paymentDate),
    ratio: typeof ratio === "number" ? ratio : null,
    cash_amount: typeof cash === "number" ? cash : null,
    currency: opt(d.currency),
    per: d.per,
    amount_basis: d.amountBasis,
    source: d.source.trim(),
    source_url: d.sourceUrl.trim(),
    evidence_id: typeof ev === "number" ? ev : null,
    status: d.status,
  };
}

export type ActionDecision = "confirmed" | "rejected" | "suggested";

/** Mirrors CorporateActionDecision, plus the route's rule that confirming needs evidence (new or already linked). */
export function validateActionDecision(decision: ActionDecision, reason: string, evidenceText: string, linkedEvidence: number | null): { reason?: string; evidence?: string } {
  const e: { reason?: string; evidence?: string } = {};
  const r = validateReason(reason);
  if (r) e.reason = r;
  const ev = parseId(evidenceText);
  if (ev === "invalid") e.evidence = "Evidence is a whole number above 0.";
  else if (decision === "confirmed" && ev === null && linkedEvidence === null) e.evidence = "Confirming needs linked source evidence. Enter its number.";
  return e;
}

export function actionDecisionBody(decision: ActionDecision, reason: string, evidenceText: string) {
  const ev = parseId(evidenceText);
  return { decision, reason: reason.trim(), evidence_id: typeof ev === "number" ? ev : null };
}
