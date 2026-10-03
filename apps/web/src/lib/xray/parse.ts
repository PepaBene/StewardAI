/*
 * Runtime guards for the three untyped Phase 6 responses. A response that is not the
 * expected shape returns null (the view shows "unavailable"); a row that fails the guard is
 * dropped and counted, never defaulted to zeros.
 */
import { AUDIT_VERDICTS, type AuditRow, type AuditVerdict, type FactsheetRow, type Parsed } from "./types";

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** number | numeric string -> number; anything else (including "" and NaN) -> null. */
export function toNum(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

export function parseFactsheet(body: unknown): Parsed<FactsheetRow> | null {
  if (!Array.isArray(body)) return null;
  const rows: FactsheetRow[] = [];
  let rejected = 0;
  for (const raw of body) {
    if (!isRecord(raw)) {
      rejected++;
      continue;
    }
    const isin = str(raw.fund_isin);
    const asOf = str(raw.as_of);
    if (!isin || !ISIN.test(isin) || !asOf || !ISO_DATE.test(asOf) || !str(raw.source)) {
      rejected++;
      continue;
    }
    rows.push({
      fundIsin: isin,
      asOf: asOf.slice(0, 10),
      source: str(raw.source) as string,
      provider: str(raw.provider),
      shareClass: str(raw.share_class),
      fundName: str(raw.fund_name),
      totalAssets: toNum(raw.total_assets),
      totalAssetsCurrency: str(raw.total_assets_currency),
      expenseRatio: toNum(raw.expense_ratio),
      benchmarkName: str(raw.benchmark_name),
      distributionPolicy: str(raw.distribution_policy),
      navBasis: str(raw.nav_basis),
      holdingsCount: toNum(raw.holdings_count),
      equityWeight: toNum(raw.equity_weight),
      cashWeight: toNum(raw.cash_weight),
      derivativeWeight: toNum(raw.derivative_weight),
      otherWeight: toNum(raw.other_weight),
      totalWeight: toNum(raw.total_weight),
      residualWeight: toNum(raw.residual_weight),
      reconciles: typeof raw.reconciles === "boolean" ? raw.reconciles : null,
      lookthroughBasis: str(raw.lookthrough_basis),
      coverageNote: str(raw.coverage_note),
      missingFields: strList(raw.missing_fields),
      fetchedAt: str(raw.fetched_at),
      // Absent flag = unverified: never assume the issuer's field names were checked.
      fieldNamesVerified: raw.field_names_verified === true,
      extractionWarnings: strList(raw.extraction_warnings),
    });
  }
  return { rows, rejected };
}

function isVerdict(v: unknown): v is AuditVerdict {
  return typeof v === "string" && (AUDIT_VERDICTS as readonly string[]).includes(v);
}

export function parseAudit(body: unknown): Parsed<AuditRow> | null {
  if (!Array.isArray(body)) return null;
  const rows: AuditRow[] = [];
  let rejected = 0;
  for (const raw of body) {
    if (!isRecord(raw)) {
      rejected++;
      continue;
    }
    const fundKey = str(raw.fund_key);
    const asOf = str(raw.as_of);
    if (!fundKey || !asOf || !ISO_DATE.test(asOf) || !isVerdict(raw.verdict) || typeof raw.alert !== "boolean") {
      rejected++;
      continue;
    }
    const detail = isRecord(raw.detail) ? raw.detail : {};
    const changesRaw = isRecord(detail.changes) ? detail.changes : {};
    const changes = Object.entries(changesRaw)
      .filter((entry): entry is [string, Record<string, unknown>] => isRecord(entry[1]))
      .map(([metric, c]) => ({ metric, from: toNum(c.from), to: toNum(c.to), relative: toNum(c.relative) }));
    rows.push({
      fundKey,
      provider: str(raw.provider),
      asOf: asOf.slice(0, 10),
      fingerprint: str(raw.fingerprint),
      basis: str(raw.basis),
      rules: isRecord(raw.rules) ? raw.rules : {},
      pe: toNum(raw.pe),
      pb: toNum(raw.pb),
      indexPe: toNum(raw.index_pe),
      indexPb: toNum(raw.index_pb),
      verdict: raw.verdict,
      alert: raw.alert,
      needsParserReview: detail.needs_parser_review === true || raw.verdict === "parser_drift",
      changes,
      note: str(detail.note),
      observedAt: str(raw.observed_at),
    });
  }
  return { rows, rejected };
}
