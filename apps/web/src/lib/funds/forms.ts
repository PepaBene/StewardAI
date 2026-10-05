import type { components } from "@/lib/api/schema";
import { type FieldErrors, parseOptionalNumber, trimOrNull, URL_RE } from "@/lib/reference/form-utils";

import { ISIN_RE } from "./funds";
import type { Tier } from "./ledger";

export type LedgerEntryRequest = components["schemas"]["LedgerEntryInput"];
export type FactsheetRequest = components["schemas"]["ManualFactsheetInput"];

/** A yes/no/unknown choice; unknown is sent as null and is never read as no. */
export type Tri = "unknown" | "yes" | "no";
export const triToBool = (t: Tri): boolean | null => (t === "unknown" ? null : t === "yes");
export const boolToTri = (b: boolean | null | undefined): Tri => (b === null || b === undefined ? "unknown" : b ? "yes" : "no");

export type LedgerDraft = {
  subjectKind: "fund" | "index";
  subjectKey: string;
  label: string;
  tier: Tier;
  reason: string;
  proxyIsin: string;
  totalReturn: Tri;
  historical: Tri;
  sourceUrl: string;
};

export const emptyLedgerDraft = (): LedgerDraft => ({
  subjectKind: "fund",
  subjectKey: "",
  label: "",
  tier: "official_holdings",
  reason: "",
  proxyIsin: "",
  totalReturn: "unknown",
  historical: "unknown",
  sourceUrl: "",
});

/** Mirrors LedgerEntryInput: key 1-80, label 1-120, reason 3-1000, ISIN pattern, URL up to 2000. */
export function validateLedger(d: LedgerDraft): FieldErrors<keyof LedgerDraft> {
  const e: FieldErrors<keyof LedgerDraft> = {};
  const key = d.subjectKey.trim();
  if (key.length < 1 || key.length > 80) e.subjectKey = "Enter 1 to 80 characters.";
  const label = d.label.trim();
  if (label.length < 1 || label.length > 120) e.label = "Enter 1 to 120 characters.";
  const reason = d.reason.trim();
  if (reason.length < 3 || reason.length > 1000) e.reason = "Say why in 3 to 1000 characters.";
  const isin = d.proxyIsin.trim().toUpperCase();
  if (d.tier === "proxy_fund" && isin === "") e.proxyIsin = "A proxy fund tier needs the proxy fund's ISIN.";
  else if (isin !== "" && !ISIN_RE.test(isin)) e.proxyIsin = "An ISIN is 2 letters, 9 letters or digits and a check digit.";
  const url = d.sourceUrl.trim();
  if (url !== "" && (!URL_RE.test(url) || url.length > 2000)) e.sourceUrl = "Enter a full http(s) address, up to 2000 characters.";
  return e;
}

export function ledgerRequest(d: LedgerDraft): LedgerEntryRequest {
  return {
    subject_kind: d.subjectKind,
    subject_key: d.subjectKey.trim(),
    label: d.label.trim(),
    tier: d.tier,
    reason: d.reason.trim(),
    proxy_fund_isin: trimOrNull(d.proxyIsin)?.toUpperCase() ?? null,
    total_return_available: triToBool(d.totalReturn),
    historical_constituents: triToBool(d.historical),
    source_url: trimOrNull(d.sourceUrl),
  };
}

export type FactsheetDraft = {
  asOf: string;
  shareClass: string;
  totalAssets: string;
  currency: string;
  /** Percent of fund value per year as typed: 0.07 means 0.07%. */
  expensePct: string;
  benchmark: string;
  distribution: "" | "accumulating" | "distributing";
  navBasis: string;
  note: string;
};

export const emptyFactsheetDraft = (asOf: string): FactsheetDraft => ({
  asOf,
  shareClass: "",
  totalAssets: "",
  currency: "",
  expensePct: "",
  benchmark: "",
  distribution: "",
  navBasis: "",
  note: "",
});

/** Mirrors ManualFactsheetInput: charge a fraction in [0, 0.2), assets >= 0, ISO currency, text caps. */
export function validateFactsheet(d: FactsheetDraft, today: string): FieldErrors<keyof FactsheetDraft> {
  const e: FieldErrors<keyof FactsheetDraft> = {};
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d.asOf)) e.asOf = "Choose the date the figures are valid for.";
  else if (d.asOf > today) e.asOf = "A factsheet cannot be dated in the future.";
  if (d.shareClass.trim().length > 80) e.shareClass = "At most 80 characters.";
  const assets = parseOptionalNumber(d.totalAssets);
  if (assets === "invalid" || (assets !== null && assets < 0)) e.totalAssets = "Enter a number of 0 or more, or leave blank.";
  const cur = d.currency.trim().toUpperCase();
  if (cur !== "" && !/^[A-Z]{3}$/.test(cur)) e.currency = "Use a 3-letter code such as EUR.";
  if (assets !== null && assets !== "invalid" && cur === "") e.currency = "Fund size needs its currency.";
  const pct = parseOptionalNumber(d.expensePct);
  if (pct === "invalid" || (pct !== null && (pct < 0 || pct >= 20))) e.expensePct = "Enter a percent from 0 to under 20, or leave blank.";
  if (d.benchmark.trim().length > 200) e.benchmark = "At most 200 characters.";
  if (d.navBasis.trim().length > 200) e.navBasis = "At most 200 characters.";
  if (d.note.trim().length > 1000) e.note = "At most 1000 characters.";
  const any = [d.shareClass, d.totalAssets, d.expensePct, d.benchmark, d.navBasis].some((v) => v.trim() !== "") || d.distribution !== "";
  if (!any && Object.keys(e).length === 0) e.asOf = "Enter at least one figure besides the date.";
  return e;
}

/** Blank fields are sent as null (not published), never 0. The percent is converted to the stored fraction. */
export function factsheetRequest(d: FactsheetDraft): FactsheetRequest {
  const assets = parseOptionalNumber(d.totalAssets);
  const pct = parseOptionalNumber(d.expensePct);
  return {
    as_of: d.asOf,
    share_class: trimOrNull(d.shareClass),
    total_assets: assets === "invalid" ? null : assets,
    total_assets_currency: assets === null || assets === "invalid" ? null : (trimOrNull(d.currency)?.toUpperCase() ?? null),
    expense_ratio: pct === null || pct === "invalid" ? null : Number((pct / 100).toPrecision(10)),
    benchmark_name: trimOrNull(d.benchmark),
    distribution_policy: d.distribution === "" ? null : d.distribution,
    nav_basis: trimOrNull(d.navBasis),
    note: trimOrNull(d.note),
  };
}

export type PinRequest = components["schemas"]["PinInput"];

/** Pin every configured fund's snapshot known at the look-through date, tied to a manual reference. */
export function pinRequest(asOf: string, reason: string): PinRequest {
  return { ref_kind: "manual", ref_id: `look-through:${asOf}`, as_of: asOf, reason: trimOrNull(reason)?.slice(0, 500) ?? null };
}

export const pinReasonError = (reason: string): string | null => (reason.trim().length > 500 ? "At most 500 characters." : null);

/* ---- characteristics audit ---- */

export type AuditVerdict = "first_observation" | "unchanged" | "normal_update" | "value_change" | "methodology_change" | "parser_drift";

export const VERDICTS: readonly { id: AuditVerdict; label: string; meaning: string }[] = [
  { id: "value_change", label: "Value changed", meaning: "The issuer's P/E or P/B moved by more than the threshold with the same page shape: a real change to check." },
  { id: "methodology_change", label: "Methodology changed", meaning: "The issuer's stated calculation rules changed, so old and new figures are not comparable." },
  { id: "parser_drift", label: "Parser drift", meaning: "The page shape changed and the figures moved: the reader may be picking up the wrong number." },
  { id: "normal_update", label: "Normal update", meaning: "A routine move within the threshold." },
  { id: "unchanged", label: "Unchanged", meaning: "Same figures as before (stored weekly as a heartbeat)." },
  { id: "first_observation", label: "First seen", meaning: "The first stored observation for this fund." },
];

export type AuditEntry = {
  key: string;
  fundKey: string;
  provider: string | null;
  asOf: string | null;
  observedAt: string | null;
  verdict: AuditVerdict | "unknown";
  alert: boolean;
  fingerprint: string | null;
  basis: string | null;
  pe: number | null;
  pb: number | null;
  indexPe: number | null;
  indexPb: number | null;
};

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const s = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const n = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
  const x = typeof v === "number" ? v : Number(v);
  return Number.isFinite(x) ? x : null;
};

export function parseAudit(payload: unknown): AuditEntry[] {
  if (!Array.isArray(payload)) return [];
  const ids = new Set<string>(VERDICTS.map((v) => v.id));
  return payload.filter(isRec).map((r, i) => {
    const verdict = s(r.verdict);
    return {
      key: `${s(r.fund_key) ?? "?"}|${s(r.observed_at) ?? i}|${i}`,
      fundKey: s(r.fund_key) ?? "Unknown",
      provider: s(r.provider),
      asOf: s(r.as_of),
      observedAt: s(r.observed_at),
      verdict: verdict && ids.has(verdict) ? (verdict as AuditVerdict) : "unknown",
      alert: r.alert === true,
      fingerprint: s(r.fingerprint),
      basis: s(r.basis),
      pe: n(r.pe),
      pb: n(r.pb),
      indexPe: n(r.index_pe),
      indexPb: n(r.index_pb),
    };
  });
}

export const verdictLabel = (v: AuditEntry["verdict"]): string => VERDICTS.find((x) => x.id === v)?.label ?? "Unknown";

export function verdictCounts(rows: readonly AuditEntry[]): { id: AuditEntry["verdict"]; label: string; count: number }[] {
  return VERDICTS.map((v) => ({ id: v.id, label: v.label, count: rows.filter((r) => r.verdict === v.id).length })).filter((c) => c.count > 0);
}

/** Funds in the audit, for the filter. */
export const auditFunds = (rows: readonly AuditEntry[]): string[] => [...new Set(rows.map((r) => r.fundKey))].sort();
