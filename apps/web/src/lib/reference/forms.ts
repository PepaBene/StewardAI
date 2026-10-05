import type { components } from "@/lib/api/schema";

import { type FieldErrors, parseOptionalNumber, trimOrNull, URL_RE, utcFromLocalInput } from "./form-utils";
import type { ForwardKind } from "./forward";

export type SessionRequest = components["schemas"]["ExchangeSessionInput"];
export type ForwardRequest = components["schemas"]["ForwardObservationInput"];

/* ---- exchange sessions ---- */

export type SessionState = "open" | "half_day" | "closed";

/** State is carried by a glyph and words, never colour alone. */
export const SESSION_STATE: Record<SessionState, { label: string; glyph: string; meaning: string }> = {
  open: { label: "Open", glyph: "●", meaning: "A full trading day." },
  half_day: { label: "Half day", glyph: "◐", meaning: "Trades, but closes early." },
  closed: { label: "Closed", glyph: "○", meaning: "No trading." },
};

export type SessionDraft = {
  mic: string;
  date: string;
  state: SessionState;
  openAt: string;
  closeAt: string;
  source: string;
  evidenceUrl: string;
  reason: string;
};

export const emptySessionDraft = (): SessionDraft => ({ mic: "", date: "", state: "half_day", openAt: "", closeAt: "", source: "", evidenceUrl: "", reason: "" });

export const needsTimes = (s: SessionState) => s !== "closed";

/** Mirrors ExchangeSessionInput: 4-char MIC, times required unless closed and close after open. */
export function validateSession(d: SessionDraft): FieldErrors<keyof SessionDraft> {
  const e: FieldErrors<keyof SessionDraft> = {};
  if (!/^[A-Z0-9]{4}$/.test(d.mic.trim().toUpperCase())) e.mic = "A market code is 4 letters or digits, such as XNYS.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.date)) e.date = "Choose the session date.";
  const open = utcFromLocalInput(d.openAt);
  const close = utcFromLocalInput(d.closeAt);
  if (needsTimes(d.state)) {
    if (!open) e.openAt = "Enter the UTC open time.";
    if (!close) e.closeAt = "Enter the UTC close time.";
  }
  if (open && close && Date.parse(close) <= Date.parse(open)) e.closeAt = "Close must be after open.";
  const source = d.source.trim();
  if (source.length < 2 || source.length > 80) e.source = "Name the source in 2 to 80 characters.";
  const url = d.evidenceUrl.trim();
  if (!URL_RE.test(url) || url.length < 8 || url.length > 2000) e.evidenceUrl = "Enter the full http(s) address of the notice, 8 to 2000 characters.";
  if (d.reason.trim().length > 300) e.reason = "At most 300 characters.";
  return e;
}

export function sessionRequest(d: SessionDraft): SessionRequest {
  const times = needsTimes(d.state);
  return {
    mic: d.mic.trim().toUpperCase(),
    session_date: d.date,
    state: d.state,
    open_at: times ? utcFromLocalInput(d.openAt) : null,
    close_at: times ? utcFromLocalInput(d.closeAt) : null,
    source: d.source.trim(),
    evidence_url: d.evidenceUrl.trim(),
    reason: trimOrNull(d.reason),
  };
}

export type SessionRow = {
  key: string;
  date: string;
  state: SessionState | "unknown";
  openAt: string | null;
  closeAt: string | null;
  source: string | null;
  confirmed: boolean;
  evidenceUrl: string | null;
  reason: string | null;
  knownAt: string | null;
};

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const s = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** Rows by date; where one day has several rows the confirmed, most recently known one comes first. */
export function parseSessions(payload: unknown): SessionRow[] {
  if (!Array.isArray(payload)) return [];
  return payload
    .filter(isRec)
    .map((r, i) => {
      const st = s(r.state);
      const date = s(r.session_date) ?? "";
      return {
        key: `${date}|${s(r.source) ?? ""}|${i}`,
        date,
        state: (st === "open" || st === "half_day" || st === "closed" ? st : "unknown") as SessionRow["state"],
        openAt: s(r.open_at),
        closeAt: s(r.close_at),
        source: s(r.source),
        confirmed: r.confirmed === true,
        evidenceUrl: s(r.evidence_url),
        reason: s(r.reason),
        knownAt: s(r.known_at),
      };
    })
    .sort((a, b) => a.date.localeCompare(b.date) || Number(b.confirmed) - Number(a.confirmed) || (b.knownAt ?? "").localeCompare(a.knownAt ?? ""));
}

export const sessionWindowError = (start: string, end: string): string | null =>
  !start || !end ? "Choose both dates." : end < start ? "The end date must not be before the start." : null;

export function stateCounts(rows: readonly SessionRow[]): { state: SessionState; count: number }[] {
  return (["open", "half_day", "closed"] as const).map((st) => ({ state: st, count: rows.filter((r) => r.state === st).length }));
}

/* ---- forward observations ---- */

/** Consensus is published by providers and has its own feed; the owner records the other kinds. */
export const ENTRY_KINDS: readonly { id: Exclude<ForwardKind, "published_analyst_consensus">; label: string; hint: string }[] = [
  { id: "company_guidance", label: "Company guidance", hint: "An outlook the company itself published. Shown apart from analyst consensus." },
  { id: "owner_scenario", label: "Your scenario", hint: "Your own assumption. Never attributed to a vendor." },
  { id: "event_calendar_estimate", label: "Calendar estimate", hint: "An estimate taken from an event calendar." },
];

export type ForwardDraft = {
  kind: (typeof ENTRY_KINDS)[number]["id"];
  provider: string;
  metric: string;
  basis: string;
  fiscalPeriod: string;
  periodEnd: string;
  currency: string;
  value: string;
  low: string;
  high: string;
  publishedAt: string;
  sourceUrl: string;
};

export const emptyForwardDraft = (): ForwardDraft => ({
  kind: "company_guidance",
  provider: "",
  metric: "",
  basis: "",
  fiscalPeriod: "",
  periodEnd: "",
  currency: "",
  value: "",
  low: "",
  high: "",
  publishedAt: "",
  sourceUrl: "",
});

/** Mirrors ForwardObservationInput: a value or range, low <= high, scenarios not vendor-attributed. */
export function validateForward(d: ForwardDraft): FieldErrors<keyof ForwardDraft> {
  const e: FieldErrors<keyof ForwardDraft> = {};
  const metric = d.metric.trim();
  if (metric.length < 1 || metric.length > 120) e.metric = "Name the metric in 1 to 120 characters.";
  const period = d.fiscalPeriod.trim();
  if (period.length < 1 || period.length > 40) e.fiscalPeriod = "Enter the period, such as FY2027, in up to 40 characters.";
  if (d.provider.trim().length > 80) e.provider = "At most 80 characters.";
  if (d.basis.trim().length > 120) e.basis = "At most 120 characters.";
  if (d.currency.trim() !== "" && !/^[A-Z]{3}$/.test(d.currency.trim().toUpperCase())) e.currency = "Use a 3-letter code such as USD.";
  const v = parseOptionalNumber(d.value);
  const lo = parseOptionalNumber(d.low);
  const hi = parseOptionalNumber(d.high);
  if (v === "invalid") e.value = "Enter a number or leave blank.";
  if (lo === "invalid") e.low = "Enter a number or leave blank.";
  if (hi === "invalid") e.high = "Enter a number or leave blank.";
  if (!e.value && !e.low && !e.high) {
    if (v === null && lo === null && hi === null) e.value = "Enter a point value or a low and high range.";
    else if (typeof lo === "number" && typeof hi === "number" && lo > hi) e.low = "Low is above high.";
  }
  if (d.kind === "owner_scenario" && d.provider.trim() !== "" && d.provider.trim() !== "owner") e.provider = "A scenario is yours; leave the source name blank.";
  if (d.kind === "company_guidance" && d.provider.trim() === "") e.provider = "Name the company or filing the guidance came from.";
  const url = d.sourceUrl.trim();
  if (!URL_RE.test(url) || url.length < 8 || url.length > 2000) e.sourceUrl = "Enter the full http(s) address, 8 to 2000 characters.";
  return e;
}

export function forwardRequest(d: ForwardDraft): ForwardRequest {
  const num = (t: string) => {
    const r = parseOptionalNumber(t);
    return r === "invalid" ? null : r;
  };
  return {
    kind: d.kind,
    provider: d.kind === "owner_scenario" ? "owner" : trimOrNull(d.provider),
    metric: d.metric.trim().toLowerCase().replaceAll(/\s+/g, "_"),
    basis: trimOrNull(d.basis),
    fiscal_period: d.fiscalPeriod.trim(),
    period_end: trimOrNull(d.periodEnd),
    currency: trimOrNull(d.currency)?.toUpperCase() ?? null,
    value: num(d.value),
    low: num(d.low),
    high: num(d.high),
    published_at: d.publishedAt ? `${d.publishedAt}T00:00:00Z` : null,
    source_url: d.sourceUrl.trim(),
  };
}
