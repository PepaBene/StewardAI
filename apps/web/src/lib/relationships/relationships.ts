import { isRecord, num, recs, str, titleCase, type Rec } from "@/lib/parse-untyped";
import { URL_RE } from "@/lib/reference/form-utils";

/* Parsers, validation and request mapping for the Relationship review page. The graph read
 * endpoints are untyped in OpenAPI, so every reader is defensive: missing stays null. */

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

export type FilingHit = { id: number; ticker: string; filer: string | null; form: string | null; filedAt: string | null; url: string | null; state: string; note: string | null; attempts: number | null };
export type Contradiction = { id: number; ticker: string; relationship: string; fiscalPeriod: string | null; kind: string; disclosureId: number | null; otherId: number | null; detail: string | null; createdAt: string | null };
export type ReviewQueue = { hits: FilingHit[]; disclosures: Disclosure[]; contradictions: Contradiction[] };

export function parseQueue(raw: unknown): ReviewQueue {
  const o = isRecord(raw) ? raw : {};
  const hits: FilingHit[] = [];
  for (const r of recs(o.filing_hits)) {
    const id = num(r.id);
    const ticker = str(r.ticker);
    if (id === null || !ticker) continue;
    hits.push({ id, ticker, filer: str(r.filer_name), form: str(r.form), filedAt: str(r.filed_at), url: str(r.url), state: str(r.review_state) ?? "pending", note: str(r.review_note), attempts: num(r.attempts) });
  }
  const disclosures = recs(o.suggested_disclosures).flatMap((r) => parseDisclosure(r) ?? []);
  const contradictions: Contradiction[] = [];
  for (const r of recs(o.contradictions)) {
    const id = num(r.id);
    const ticker = str(r.ticker);
    if (id === null || !ticker) continue;
    const d = r.detail;
    contradictions.push({
      id,
      ticker,
      relationship: str(r.relationship) ?? "unknown",
      fiscalPeriod: str(r.fiscal_period),
      kind: str(r.kind) ?? "unknown",
      disclosureId: num(r.disclosure_id),
      otherId: num(r.other_disclosure_id),
      detail: typeof d === "string" ? d : isRecord(d) ? Object.entries(d).map(([k, v]) => `${titleCase(k)}: ${String(v)}`).join("; ") : null,
      createdAt: str(r.created_at),
    });
  }
  return { hits, disclosures, contradictions };
}

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

export function parseReconciliations(raw: unknown): Reconciliation[] {
  const o = isRecord(raw) ? raw : {};
  const out: Reconciliation[] = [];
  for (const r of recs(o.open)) {
    const id = num(r.id);
    const ticker = str(r.ticker);
    if (id === null || !ticker) continue;
    out.push({ id, ticker, changeKind: str(r.change_kind) ?? "change", fromKey: str(r.from_key), toKey: str(r.to_key), source: str(r.observed_source), observedAt: str(r.observed_at), outcome: str(r.outcome) ?? "unexplained" });
  }
  return out;
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
