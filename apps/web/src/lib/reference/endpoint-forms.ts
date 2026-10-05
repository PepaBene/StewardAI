/** Pure helpers for the owner forms and read-only views over the free-source endpoints. They mirror the pydantic models in apps/backend/src/api. */
import { isRecord, num, str, recs, type Rec } from "@/lib/parse-untyped";

import { parseOptionalNumber, trimOrNull, URL_RE } from "./form-utils";

type Errs<K extends string> = Partial<Record<K, string>>;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** A blank id is null (not entered); anything else must be a positive whole number. */
export function parseId(text: string): number | null | "invalid" {
  const t = text.trim();
  if (t === "") return null;
  return /^\d+$/.test(t) && Number(t) >= 1 ? Number(t) : "invalid";
}

const badDate = (t: string) => t.trim() !== "" && !DATE_RE.test(t.trim());

// ---- Peer set -------------------------------------------------------------------------------
export type PeerDraft = {
  peer: string;
  basis: string;
  source: string;
  status: "suggested" | "confirmed";
  evidenceId: string;
  validFrom: string;
  validTo: string;
};
export const emptyPeerDraft = (): PeerDraft => ({ peer: "", basis: "", source: "owner", status: "suggested", evidenceId: "", validFrom: "", validTo: "" });

export function validatePeer(d: PeerDraft, holding: string): Errs<keyof PeerDraft> {
  const e: Errs<keyof PeerDraft> = {};
  if (!/^[A-Za-z0-9._-]{1,32}$/.test(d.peer.trim())) e.peer = "Use 1 to 32 letters, digits, dots, dashes or underscores.";
  else if (d.peer.trim().toLowerCase() === holding.toLowerCase()) e.peer = "A holding cannot be its own peer.";
  if (d.basis.trim().length < 2 || d.basis.trim().length > 80) e.basis = "Say why it is a peer, 2 to 80 characters.";
  if (d.source.trim().length < 2 || d.source.trim().length > 80) e.source = "Name the source, 2 to 80 characters.";
  const ev = parseId(d.evidenceId);
  if (ev === "invalid") e.evidenceId = "Evidence id must be a whole number.";
  else if (d.status === "confirmed" && ev === null) e.evidenceId = "Confirming a peer needs the id of its supporting evidence.";
  if (badDate(d.validFrom)) e.validFrom = "Use YYYY-MM-DD.";
  if (badDate(d.validTo)) e.validTo = "Use YYYY-MM-DD.";
  if (!e.validFrom && !e.validTo && d.validFrom.trim() && d.validTo.trim() && d.validTo.trim() < d.validFrom.trim()) e.validTo = "The end precedes the start.";
  return e;
}

export function peerRequest(d: PeerDraft) {
  const ev = parseId(d.evidenceId);
  return {
    peer_ticker: d.peer.trim(),
    basis: d.basis.trim(),
    source: d.source.trim(),
    status: d.status,
    evidence_id: typeof ev === "number" ? ev : null,
    valid_from: trimOrNull(d.validFrom),
    valid_to: trimOrNull(d.validTo),
  };
}

/** A status change needs a reason of 3 to 2000 characters, and confirming needs evidence on the peer or in the request. */
export function validatePeerStatus(
  status: "suggested" | "confirmed" | "rejected",
  reason: string,
  evidenceId: string,
  peerHasEvidence: boolean,
): Errs<"reason" | "evidenceId"> {
  const e: Errs<"reason" | "evidenceId"> = {};
  const r = reason.trim();
  if (r.length < 3 || r.length > 2000) e.reason = "Give a reason, 3 to 2000 characters.";
  const ev = parseId(evidenceId);
  if (ev === "invalid") e.evidenceId = "Evidence id must be a whole number.";
  else if (status === "confirmed" && ev === null && !peerHasEvidence) e.evidenceId = "This peer has no evidence on file; enter an evidence id to confirm it.";
  return e;
}

export type PeerEntry = {
  id: number;
  ticker: string;
  basis: string | null;
  source: string | null;
  status: "suggested" | "confirmed" | "rejected";
  evidenceId: number | null;
  validFrom: string | null;
  validTo: string | null;
  history: { status: string; reason: string | null; at: string | null }[];
};

export function parsePeerSet(raw: unknown): PeerEntry[] {
  if (!isRecord(raw)) return [];
  return recs(raw.peers).flatMap((p) => {
    const id = num(p.id);
    const ticker = str(p.peer_ticker);
    const status = str(p.status);
    if (id === null || !ticker) return [];
    const s = status === "confirmed" || status === "rejected" ? status : "suggested";
    const history = Array.isArray(p.status_history)
      ? recs(p.status_history).map((h) => ({ status: str(h.status) ?? "unknown", reason: str(h.reason), at: str(h.at) ?? str(h.changed_at) ?? str(h.known_at) }))
      : [];
    return [{ id, ticker, basis: str(p.basis), source: str(p.source), status: s, evidenceId: num(p.evidence_id), validFrom: str(p.valid_from), validTo: str(p.valid_to), history }];
  });
}

// ---- Macro exposure --------------------------------------------------------------------------
export type ExposureDraft = {
  issuerId: string;
  segment: string;
  kind: "geography" | "product";
  geography: string;
  hsRevision: string;
  hsCode: string;
  basis: "reported" | "estimated" | "user_assigned";
  weightPct: string;
  validFrom: string;
  validTo: string;
};
export const emptyExposureDraft = (issuerId = ""): ExposureDraft => ({
  issuerId,
  segment: "",
  kind: "geography",
  geography: "",
  hsRevision: "HS2022",
  hsCode: "",
  basis: "user_assigned",
  weightPct: "",
  validFrom: "",
  validTo: "",
});

export function validateExposure(d: ExposureDraft): Errs<keyof ExposureDraft> {
  const e: Errs<keyof ExposureDraft> = {};
  const id = parseId(d.issuerId);
  if (id === null || id === "invalid") e.issuerId = "Enter the issuer id (a whole number).";
  if (d.segment.trim().length < 1 || d.segment.trim().length > 160) e.segment = "Name the revenue segment, up to 160 characters.";
  if (d.kind === "geography") {
    if (!/^[A-Z0-9]{2,5}$/.test(d.geography.trim().toUpperCase())) e.geography = "Use a 2 to 5 character code such as US or DE.";
  } else {
    if (!/^[0-9]{2,6}$/.test(d.hsCode.trim())) e.hsCode = "Use a 2 to 6 digit HS code.";
    if (d.hsRevision.trim() === "") e.hsRevision = "A product code needs its HS revision, e.g. HS2022.";
  }
  const w = parseOptionalNumber(d.weightPct);
  if (w === null || w === "invalid") e.weightPct = "Enter the share of revenue as a percentage.";
  else if (w < 0 || w > 100) e.weightPct = "A share is between 0 and 100 percent.";
  if (!DATE_RE.test(d.validFrom.trim())) e.validFrom = "Use YYYY-MM-DD.";
  if (badDate(d.validTo)) e.validTo = "Use YYYY-MM-DD.";
  else if (!e.validFrom && d.validTo.trim() && d.validTo.trim() < d.validFrom.trim()) e.validTo = "The end precedes the start.";
  return e;
}

export function exposureRequest(d: ExposureDraft) {
  const w = parseOptionalNumber(d.weightPct);
  const geo = d.kind === "geography";
  return {
    issuer_id: Number(d.issuerId.trim()),
    segment: d.segment.trim(),
    geography: geo ? d.geography.trim().toUpperCase() : null,
    hs_revision: geo ? null : d.hsRevision.trim(),
    hs_code: geo ? null : d.hsCode.trim(),
    basis: d.basis,
    weight: String(typeof w === "number" ? Math.round(w * 1e6) / 1e8 : 0),
    valid_from: d.validFrom.trim(),
    valid_to: trimOrNull(d.validTo),
  };
}

type MappingRow = { weight: number | string; geography?: string | null; hs_code?: string | null };

/** Sum of mapped weights per denominator (geography or product): over 100 percent is flagged; under stays unknown, never filled. */
export function mappedShare(rows: MappingRow[]) {
  const sum = (pick: (r: MappingRow) => boolean) => rows.filter(pick).reduce((a, r) => a + (num(r.weight) ?? 0), 0);
  const geography = sum((r) => !!r.geography && !r.hs_code);
  const product = sum((r) => !!r.hs_code);
  return { geography, product, geographyOver: geography > 1.0000001, productOver: product > 1.0000001 };
}

// ---- Identity and listing --------------------------------------------------------------------
export type OverrideDraft = {
  decision: "confirmed" | "rejected" | "unresolved";
  issuerId: string;
  securityId: string;
  listingId: string;
  reason: string;
};
export const emptyOverrideDraft = (): OverrideDraft => ({ decision: "confirmed", issuerId: "", securityId: "", listingId: "", reason: "" });

export function validateOverride(d: OverrideDraft): Errs<keyof OverrideDraft> {
  const e: Errs<keyof OverrideDraft> = {};
  for (const k of ["issuerId", "securityId", "listingId"] as const) if (parseId(d[k]) === "invalid") e[k] = "A whole number of 1 or more, or blank.";
  if (d.issuerId.trim() === "" && d.securityId.trim() === "" && d.listingId.trim() === "") e.issuerId = "Say what you are deciding on: an issuer, security or listing id.";
  if (d.reason.trim().length < 1 || d.reason.trim().length > 500) e.reason = "Give a reason, up to 500 characters.";
  return e;
}

export function overrideRequest(d: OverrideDraft) {
  const id = (t: string) => {
    const v = parseId(t);
    return typeof v === "number" ? v : null;
  };
  return { decision: d.decision, issuer_id: id(d.issuerId), security_id: id(d.securityId), listing_id: id(d.listingId), reason: d.reason.trim() };
}

export function validateListingDecision(reason: string, listingId: string): Errs<"reason" | "listingId"> {
  const e: Errs<"reason" | "listingId"> = {};
  if (reason.trim().length < 10 || reason.trim().length > 2000) e.reason = "Give a reason, 10 to 2000 characters.";
  if (parseId(listingId) === "invalid") e.listingId = "A whole number, or blank to use the derived listing.";
  return e;
}

// ---- Patent families -------------------------------------------------------------------------
export type PatentDraft = {
  familyKey: string;
  title: string;
  applicant: string;
  priorityDate: string;
  legalStatus: "application" | "granted" | "active" | "expired" | "unknown";
  cpc: string;
  jurisdictions: string;
  fwd: string;
  bwd: string;
  source: string;
  sourceUrl: string;
  confidence: string;
  basis: "direct" | "dated_subsidiary_path" | "suggested";
  evidenceId: string;
};
export const emptyPatentDraft = (): PatentDraft => ({
  familyKey: "",
  title: "",
  applicant: "",
  priorityDate: "",
  legalStatus: "unknown",
  cpc: "",
  jurisdictions: "",
  fwd: "",
  bwd: "",
  source: "owner",
  sourceUrl: "",
  confidence: "",
  basis: "suggested",
  evidenceId: "",
});

const splitList = (t: string) =>
  t
    .split(/[,\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export function validatePatent(d: PatentDraft): Errs<keyof PatentDraft> {
  const e: Errs<keyof PatentDraft> = {};
  if (d.familyKey.trim().length < 1 || d.familyKey.trim().length > 160) e.familyKey = "Enter the family key, up to 160 characters.";
  if (d.applicant.trim().length < 1 || d.applicant.trim().length > 240) e.applicant = "Name the applicant, up to 240 characters.";
  if (d.title.length > 2000) e.title = "Up to 2000 characters.";
  if (d.source.trim().length < 2 || d.source.trim().length > 80) e.source = "Name the source, 2 to 80 characters.";
  if (d.sourceUrl.trim().length < 8 || !URL_RE.test(d.sourceUrl.trim())) e.sourceUrl = "Enter a full http(s) link to the source.";
  if (badDate(d.priorityDate)) e.priorityDate = "Use YYYY-MM-DD.";
  for (const k of ["fwd", "bwd"] as const) if (d[k].trim() !== "" && !/^\d+$/.test(d[k].trim())) e[k] = "A whole number of 0 or more, or blank.";
  const c = parseOptionalNumber(d.confidence);
  if (c === null || c === "invalid") e.confidence = "Enter your confidence in the ownership, 0 to 1.";
  else if (c < 0 || c > 1) e.confidence = "Between 0 and 1.";
  const ev = parseId(d.evidenceId);
  if (ev === "invalid") e.evidenceId = "Evidence id must be a whole number.";
  else if (d.basis === "direct" && ev === null) e.evidenceId = "Direct attribution needs primary ownership evidence.";
  if (d.basis === "dated_subsidiary_path" && d.priorityDate.trim() === "") e.priorityDate = "Subsidiary attribution needs the priority date.";
  if (splitList(d.cpc).length > 100) e.cpc = "At most 100 codes.";
  return e;
}

export function patentRequest(d: PatentDraft) {
  const ev = parseId(d.evidenceId);
  const c = parseOptionalNumber(d.confidence);
  return {
    family_key: d.familyKey.trim(),
    title: trimOrNull(d.title),
    applicant: d.applicant.trim(),
    priority_date: trimOrNull(d.priorityDate),
    cpc_codes: splitList(d.cpc),
    jurisdictions: splitList(d.jurisdictions).map((j) => j.toUpperCase()),
    forward_citations: d.fwd.trim() === "" ? 0 : Number(d.fwd),
    backward_citations: d.bwd.trim() === "" ? 0 : Number(d.bwd),
    legal_status: d.legalStatus,
    source: d.source.trim(),
    source_url: d.sourceUrl.trim(),
    ownership_confidence: String(typeof c === "number" ? c : 0),
    ownership_basis: d.basis,
    evidence_id: typeof ev === "number" ? ev : null,
  };
}

// ---- Returns ---------------------------------------------------------------------------------
export type ReturnBar = { id: "raw" | "adjusted" | "total"; label: string; value: number | null; reason: string | null };
export type ReturnsView = {
  symbol: string | null;
  currency: string | null;
  start: string | null;
  end: string | null;
  bars: ReturnBar[];
  actionCoverage: string | null;
  coverage: number | null;
  expected: number | null;
  observed: number | null;
  missingActions: number | null;
  reason: string | null;
} | null;

export function parseReturns(raw: unknown): ReturnsView {
  if (!isRecord(raw)) return null;
  const r = recs(raw.results)[0];
  if (!r) {
    return { symbol: null, currency: null, start: null, end: null, bars: [], actionCoverage: null, coverage: null, expected: null, observed: null, missingActions: null, reason: str(raw.reason) ?? "No return could be computed." };
  }
  const total = num(r.total_return);
  return {
    symbol: str(r.symbol),
    currency: str(r.currency),
    start: str(r.start),
    end: str(r.end),
    reason: null,
    bars: [
      { id: "raw", label: "Raw price", value: num(r.raw_price_return), reason: null },
      { id: "adjusted", label: "Adjusted price", value: num(r.price_return), reason: str(r.price_return_reason) },
      { id: "total", label: "Total return", value: total, reason: total === null ? (str(r.total_return_reason) ?? "Unavailable") : null },
    ],
    actionCoverage: str(r.action_coverage),
    coverage: num(r.coverage),
    expected: num(r.expected_sessions),
    observed: num(r.observed_sessions),
    missingActions: Array.isArray(r.missing_actions) ? r.missing_actions.length : num(r.missing_actions),
  };
}

/** A cell of a record as short text; objects collapse to a count so nothing is dumped raw. Missing is "Unknown". */
export function cellText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "Unknown";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return `${v.length} item${v.length === 1 ? "" : "s"}`;
  if (isRecord(v)) return `${Object.keys(v).length} field${Object.keys(v).length === 1 ? "" : "s"}`;
  return String(v);
}

/** The list inside an untyped response: the first array of records among the named keys, else any array of records. */
export function listIn(raw: unknown, keys: string[]): Rec[] {
  if (Array.isArray(raw)) return recs(raw);
  if (!isRecord(raw)) return [];
  for (const k of keys) if (Array.isArray(raw[k])) return recs(raw[k]);
  for (const v of Object.values(raw)) if (Array.isArray(v) && v.length > 0 && isRecord(v[0])) return recs(v);
  return [];
}

// ---- Jobs and provider triggers --------------------------------------------------------------
export const JOBS = [
  { path: "/jobs/control-stakes", label: "Control stakes", note: "Reads public ownership filings for control stakes." },
  { path: "/jobs/nport-holdings", label: "Fund holdings (N-PORT)", note: "Fetches the latest SEC N-PORT fund holdings." },
  { path: "/jobs/public-data", label: "Public data", note: "Refreshes the free public datasets." },
  { path: "/jobs/sec-xbrl", label: "SEC XBRL facts", note: "Pulls structured filing facts. Optionally one ticker, optionally forced." },
  { path: "/jobs/source-audit", label: "Source audit", note: "Tests every provider endpoint with its free key, so it spends free-tier calls on each provider." },
  { path: "/jobs/etf-look-through", label: "ETF look-through", note: "Resolves ETF holdings into the underlying companies." },
  { path: "/jobs/free-source-alerts", label: "Free-source alerts", note: "Evaluates the free-source alert rules." },
  { path: "/jobs/fact-reconcile", label: "Fact reconcile", note: "Cross-checks stored facts. Optionally one ticker." },
] as const;
export type JobPath = (typeof JOBS)[number]["path"];
export const jobTakesTicker = (p: JobPath) => p === "/jobs/sec-xbrl" || p === "/jobs/fact-reconcile";

export const AV_FUNCTIONS = ["WTI", "BRENT", "NATURAL_GAS", "COPPER", "ALUMINUM", "WHEAT", "CORN", "COFFEE", "SUGAR", "FX_DAILY"] as const;
export function validateAv(fn: string, series: string, from: string, to: string): Errs<"series" | "from" | "to"> {
  const e: Errs<"series" | "from" | "to"> = {};
  if (series.trim() === "") e.series = "Name the series, e.g. Brent crude.";
  if (fn === "FX_DAILY") {
    if (!/^[A-Za-z]{3}$/.test(from.trim())) e.from = "A 3-letter currency code.";
    if (!/^[A-Za-z]{3}$/.test(to.trim())) e.to = "A 3-letter currency code.";
  }
  return e;
}
