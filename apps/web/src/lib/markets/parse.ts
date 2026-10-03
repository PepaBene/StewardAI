import {
  ACTION_TYPES,
  type ActionStatus,
  type ActionType,
  type CorporateAction,
  type ExchangeSessionRow,
  type ExpectedSessions,
  type ListingOverview,
  type Parsed,
  type ReturnFigure,
  type ReturnsReport,
  type ReturnsResult,
} from "./types";

/*
 * Narrowing of the untyped bodies (`unknown` in OpenAPI) into typed values. Anything that does
 * not match is rejected with a reason, never defaulted: a missing number stays null and a
 * malformed row makes the whole body `unavailable` rather than showing a partial guess.
 */

type Rec = Record<string, unknown>;

const fail = <T>(reason: string): Parsed<T> => ({ ok: false, reason });
const ok = <T>(data: T): Parsed<T> => ({ ok: true, data });

export function isRec(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A finite number, from a JSON number or a numeric string (Decimals may arrive as either). */
export function asNumber(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function asString(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

/** The YYYY-MM-DD part of an ISO date or datetime; null for anything else. */
export function asDay(v: unknown): string | null {
  return typeof v === "string" && ISO_DAY.test(v) ? v.slice(0, 10) : null;
}

const STATUSES: readonly ActionStatus[] = ["suggested", "confirmed", "rejected", "disputed"];

function parseRaw(v: unknown): Rec {
  if (isRec(v)) return v;
  if (typeof v === "string") {
    try {
      const parsed: unknown = JSON.parse(v);
      return isRec(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

export function parseCorporateActions(body: unknown): Parsed<CorporateAction[]> {
  if (!Array.isArray(body)) return fail("corporate-actions: expected a list");
  const out: CorporateAction[] = [];
  for (let i = 0; i < body.length; i++) {
    const r = body[i];
    if (!isRec(r)) return fail(`corporate-actions: row ${i} is not an object`);
    const id = asNumber(r.id);
    const type = asString(r.action_type);
    const exDate = asDay(r.ex_date);
    const status = asString(r.status);
    const source = asString(r.source);
    if (id === null || !type || !exDate || !status || !source) return fail(`corporate-actions: row ${i} lacks id, type, ex-date, status or source`);
    if (!(ACTION_TYPES as readonly string[]).includes(type)) return fail(`corporate-actions: row ${i} has unknown type "${type}"`);
    if (!(STATUSES as readonly string[]).includes(status)) return fail(`corporate-actions: row ${i} has unknown status "${status}"`);
    const raw = parseRaw(r.raw);
    out.push({
      id,
      symbol: asString(r.symbol) ?? "",
      type: type as ActionType,
      exDate,
      recordDate: asDay(r.record_date),
      paymentDate: asDay(r.payment_date),
      ratio: asNumber(r.ratio),
      cashAmount: asNumber(r.cash_amount),
      currency: asString(r.currency),
      source,
      sourceUrl: asString(r.source_url),
      knownAt: asString(r.known_at),
      evidenceId: asNumber(r.evidence_id),
      status: status as ActionStatus,
      decidedAt: asString(r.decided_at),
      decisionReason: asString(r.decision_reason),
      per: asString(raw.per),
      amountBasis: asString(raw.amount_basis),
    });
  }
  return ok(out);
}

function figure(value: unknown, reason: unknown): ReturnFigure {
  return { value: asNumber(value), reason: asString(reason) };
}

function joined(list: readonly string[]): string | null {
  return list.length > 0 ? list.join("; ") : null;
}

function stringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

export function parseReturns(body: unknown): Parsed<ReturnsReport> {
  if (!isRec(body)) return fail("returns: expected an object");
  const ticker = asString(body.ticker);
  if (!ticker || !Array.isArray(body.results)) return fail("returns: ticker or results missing");
  const knownAsOf = asDay(body.known_as_of);
  if (body.results.length === 0) return ok({ ticker, knownAsOf, result: null, reason: asString(body.reason) ?? "No return could be computed." });
  const r = body.results[0];
  if (!isRec(r)) return fail("returns: result is not an object");
  const start = asDay(r.start);
  const end = asDay(r.end);
  const symbol = asString(r.symbol);
  const source = asString(r.source);
  if (!start || !end || !symbol || !source) return fail("returns: result lacks window, symbol or source");
  const cov = r.action_coverage;
  if (cov !== "available" && cov !== "incomplete") return fail("returns: unknown action_coverage");
  const missing = isRec(r.missing_actions) ? r.missing_actions : {};
  const missingLists = { price_raw: stringList(missing.price_raw), price_adjusted: stringList(missing.price_adjusted), total_return: stringList(missing.total_return) };
  const result: ReturnsResult = {
    symbol,
    source,
    currency: asString(r.currency),
    mic: asString(r.mic),
    // The route returns a reason only for adjusted and total return; the raw return's reason is
    // the backend's `missing_actions.price_raw` list.
    priceReturn: figure(r.price_return, asString(r.price_return_reason) ?? joined(missingLists.price_adjusted)),
    rawPriceReturn: figure(r.raw_price_return, asString(r.raw_price_return_reason) ?? joined(missingLists.price_raw)),
    totalReturn: figure(r.total_return, asString(r.total_return_reason) ?? joined(missingLists.total_return)),
    actionCoverage: cov,
    coverage: asNumber(r.coverage),
    expectedSessions: asNumber(r.expected_sessions),
    observedSessions: asNumber(r.observed_sessions),
    calendarBasis: asString(r.calendar_basis),
    missingActions: { priceRaw: stringList(missing.price_raw), priceAdjusted: stringList(missing.price_adjusted), totalReturn: stringList(missing.total_return) },
    start,
    end,
  };
  return ok({ ticker, knownAsOf, result, reason: null });
}

export function parseListing(body: unknown): Parsed<ListingOverview> {
  if (!isRec(body)) return fail("listing: expected an object");
  const ticker = asString(body.ticker);
  const status = asString(body.status);
  if (!ticker || !status) return fail("listing: ticker or status missing");
  let listing: ListingOverview["listing"] = null;
  if (body.listing !== null && body.listing !== undefined) {
    if (!isRec(body.listing)) return fail("listing: listing is not an object");
    const id = asNumber(body.listing.id);
    if (id === null) return fail("listing: listing id missing");
    listing = {
      id,
      mic: asString(body.listing.mic),
      ticker: asString(body.listing.ticker),
      currency: asString(body.listing.currency),
      status: asString(body.listing.status),
      isin: asString(body.listing.isin),
    };
  }
  const symbols = Array.isArray(body.provider_symbols) ? body.provider_symbols : [];
  const providerSymbols: ListingOverview["providerSymbols"] = [];
  for (const s of symbols) {
    if (!isRec(s)) return fail("listing: provider symbol is not an object");
    const provider = asString(s.provider);
    const identifier = asString(s.identifier);
    if (!provider || !identifier) return fail("listing: provider symbol lacks provider or identifier");
    providerSymbols.push({ provider, identifier, matchStatus: asString(s.match_status) });
  }
  return ok({ ticker, status, score: asNumber(body.score), decidedAt: asString(body.decided_at), listing, providerSymbols });
}

export function parseExchangeSessions(body: unknown): Parsed<ExchangeSessionRow[]> {
  if (!Array.isArray(body)) return fail("exchange-sessions: expected a list");
  const out: ExchangeSessionRow[] = [];
  for (let i = 0; i < body.length; i++) {
    const r = body[i];
    if (!isRec(r)) return fail(`exchange-sessions: row ${i} is not an object`);
    const mic = asString(r.mic);
    const sessionDate = asDay(r.session_date);
    const state = r.state;
    if (!mic || !sessionDate || (state !== "open" && state !== "half_day" && state !== "closed")) return fail(`exchange-sessions: row ${i} lacks mic, date or a known state`);
    out.push({
      mic,
      sessionDate,
      state,
      source: asString(r.source) ?? "unknown",
      confirmed: r.confirmed === true,
      reason: asString(r.reason),
      evidenceUrl: asString(r.evidence_url),
      knownAt: asString(r.known_at),
    });
  }
  return ok(out);
}

export function parseExpectedSessions(body: unknown): Parsed<ExpectedSessions> {
  if (!isRec(body)) return fail("expected sessions: expected an object");
  const mic = asString(body.mic);
  const start = asDay(body.start);
  const end = asDay(body.end);
  const basis = asString(body.basis);
  const count = asNumber(body.count);
  if (!mic || !start || !end || !basis || count === null || !Array.isArray(body.days)) return fail("expected sessions: mic, window, basis, count or days missing");
  const days: string[] = [];
  for (const d of body.days) {
    const day = asDay(d);
    if (!day) return fail("expected sessions: a day is not a date");
    days.push(day);
  }
  return ok({ mic, start, end, count, basis, confirmedDays: asNumber(body.confirmed_days) ?? 0, days });
}
