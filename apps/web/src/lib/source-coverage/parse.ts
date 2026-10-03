import type { components } from "@/lib/api/schema";
import { stateWithCoverage, type DataCoverage } from "@/lib/data-state/provenance";
import { toDataState, type DataState } from "@/lib/data-state/states";

import type {
  AccessFreshness,
  AccessRow,
  CoverageEntry,
  EntityKind,
  GateFinding,
  StaleBasis,
  SourceStatusRow,
  UsageWindow,
  ZeroCostGate,
} from "./types";

type Schemas = components["schemas"];

/** A Decimal field arrives as a number or a numeric string; anything else is "unknown", never 0. */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * The parenthetical that says where a stale threshold comes from, in the backend's own terms:
 * both bases are twice a refresh gap (config/jobs.py COVERAGE_STALE_GRACE); `job_schedule` is the
 * refreshing job's cron, `documented_default` the documented interval table. null basis -> no claim.
 */
export function staleBasisPhrase(basis: StaleBasis | null): string | null {
  if (basis === "job_schedule") return "twice the scheduled refresh gap";
  if (basis === "documented_default") return "twice the documented default refresh interval";
  return null;
}

const ENTITY_KINDS: readonly EntityKind[] = ["security", "geography", "other"];

/**
 * `GET /data/coverage` row -> CoverageEntry. An available entry with ratio < 1 is "partial".
 * A usable (verified / partial) entry whose backend `stale` flag is true reads "stale" with the
 * reason "last checked N h ago, older than X h (twice the scheduled refresh gap)" for a job-schedule basis or "(twice the documented default refresh interval)" for a documented default; gap states (failed, paid only, ...)
 * keep their own state. `stale` null = age not judged (no job cadence known), never fresh.
 */
export function adaptCoverage(row: Schemas["DataCoverage"], now: number = Date.now()): CoverageEntry {
  const expected = toNumber(row.expected_periods);
  const available = toNumber(row.available_periods);
  const ratio = toNumber(row.coverage_ratio);
  const base = toDataState(row.state);
  const coverage: DataCoverage | null =
    ratio !== null && expected !== null && expected > 0 && available !== null
      ? { covered: available, total: expected, unit: "periods" }
      : null;
  const stale = row.stale === true ? true : row.stale === false ? false : null;
  const staleAfterHours = toNumber(row.stale_after_hours);
  const stateAfterCoverage = stateWithCoverage(base, coverage);
  const usable = stateAfterCoverage === "verified" || stateAfterCoverage === "partial";
  const checkedMs = Date.parse(row.checked_at);
  const ageHours = Number.isFinite(checkedMs) ? Math.max(0, Math.round((now - checkedMs) / 3_600_000)) : null;
  const staleBasis: StaleBasis | null = row.stale_basis === "job_schedule" || row.stale_basis === "documented_default" ? row.stale_basis : null;
  const phrase = staleBasisPhrase(staleBasis);
  const staleReason =
    stale === true && staleAfterHours !== null
      ? `last checked ${ageHours === null ? "an unknown time" : `${ageHours} h`} ago, older than ${staleAfterHours} h${phrase ? ` (${phrase})` : ""}`
      : null;
  return {
    id: `${row.provider}:${row.dataset}:${row.entity_key}`,
    provider: row.provider,
    dataset: row.dataset,
    entityKey: row.entity_key,
    entityKind: ENTITY_KINDS.includes(row.entity_kind) ? row.entity_kind : "other",
    state: stale === true && usable ? "stale" : stateAfterCoverage,
    stale,
    staleAfterHours,
    staleBasis,
    staleReason: stale === true && usable ? staleReason ?? "last check is older than the expected interval" : null,
    rawState: row.state,
    expectedPeriods: expected,
    availablePeriods: available,
    coverageRatio: ratio,
    earliestPeriod: row.earliest_period ?? null,
    latestPeriod: row.latest_period ?? null,
    ageDays: toNumber(row.age_days),
    checkedAt: row.checked_at,
    lastGoodObservationId: toNumber(row.last_good_observation_id),
    reason: row.reason ?? null,
  };
}

/**
 * The access state a view should show. The ledger's `available` is verified only while a
 * successful call is recent: stale -> "stale"; never succeeded -> "access_unverified" (no
 * observation has ever been collected through it). Other ledger states pass through the
 * shared adapter.
 */
export function accessState(rawState: string, freshness: AccessFreshness): DataState {
  const base = toDataState(rawState);
  if (base !== "verified") return base;
  if (freshness === "stale") return "stale";
  if (freshness === "never_succeeded") return "access_unverified";
  return "verified";
}

export function adaptAccess(row: Schemas["SourceAccess"]): AccessRow {
  return {
    id: `${row.provider}/${row.endpoint}`,
    provider: row.provider,
    endpoint: row.endpoint,
    accountTier: row.account_tier,
    state: accessState(row.state, row.freshness),
    rawState: row.state,
    freshness: row.freshness,
    lastSuccessAt: row.last_success_at ?? null,
    lastSuccessAgeSeconds: toNumber(row.last_success_age_seconds),
    staleAfterHours: row.stale_after_hours ?? 72,
    testedAt: row.tested_at ?? null,
    retryAt: row.retry_at ?? null,
    hardBlocked: row.hard_blocked,
    detail: row.detail ?? null,
    lastDenialAt: row.last_denial_at ?? null,
    lastDenialState: row.last_denial_state ?? null,
    lastDenialStatusCode: toNumber(row.last_denial_status_code),
    lastDenialDetail: row.last_denial_detail ?? null,
    quota: toNumber(row.quota),
    quotaPeriod: row.quota_period ?? null,
    resetTimezone: row.reset_timezone ?? null,
    requestWeight: toNumber(row.request_weight),
    quotaWindowStart: row.quota_window_start ?? null,
    quotaUsed: toNumber(row.quota_used),
    quotaRemaining: toNumber(row.quota_remaining),
    quotaHeadroom: toNumber(row.quota_headroom),
    headroomReason: row.headroom_reason ?? null,
  };
}

export function adaptUsage(row: Schemas["SourceUsage"]): UsageWindow {
  return {
    provider: row.provider,
    endpoint: row.endpoint,
    windowKind: row.window_kind,
    windowStart: row.window_start,
    windowEnd: row.window_end,
    credits: toNumber(row.credits) ?? 0,
    requests: row.requests,
  };
}

export function adaptStatus(row: Schemas["ProviderStatus"]): SourceStatusRow {
  return {
    provider: row.provider,
    endpoint: row.endpoint,
    rawState: row.state,
    state: toDataState(row.state),
    hardBlocked: row.hard_blocked,
    quota: toNumber(row.quota),
    quotaPeriod: row.quota_period ?? null,
    lastSuccessAt: row.last_success_at ?? null,
    retryAt: row.retry_at ?? null,
    dayCredits: toNumber(row.day_credits) ?? 0,
    monthCredits: toNumber(row.month_credits) ?? 0,
  };
}

// ---------------------------------------------------------------------------------------
// Zero-cost gate (untyped in OpenAPI: parsed from `unknown`)
// ---------------------------------------------------------------------------------------

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function count(v: unknown): number | null {
  const n = toNumber(v);
  return n !== null && Number.isInteger(n) && n >= 0 ? n : null;
}

/**
 * Narrows the zero-cost-gate body. Any unexpected shape returns null (shown as unavailable)
 * rather than a "passed" gate: a malformed answer must never read as a clean audit.
 */
export function parseZeroCostGate(body: unknown): ZeroCostGate | null {
  if (!isRecord(body) || typeof body.passed !== "boolean") return null;
  const checked = count(body.checked);
  const freeAvailable = count(body.free_available);
  const disabledOk = count(body.disabled_ok);
  const gatedUnverified = count(body.gated_unverified);
  const violationCount = count(body.violation_count);
  const warningCount = count(body.warning_count);
  if ([checked, freeAvailable, disabledOk, gatedUnverified, violationCount, warningCount].some((n) => n === null)) return null;
  if (!Array.isArray(body.findings)) return null;
  const findings: GateFinding[] = [];
  for (const f of body.findings) {
    if (
      !isRecord(f) ||
      typeof f.provider !== "string" ||
      typeof f.endpoint !== "string" ||
      (f.severity !== "violation" && f.severity !== "warning") ||
      typeof f.code !== "string" ||
      typeof f.message !== "string"
    ) {
      return null;
    }
    findings.push({ provider: f.provider, endpoint: f.endpoint, severity: f.severity, code: f.code, message: f.message });
  }
  const warningsByCode: Record<string, number> = {};
  if (isRecord(body.warnings_by_code)) {
    for (const [k, v] of Object.entries(body.warnings_by_code)) {
      const n = count(v);
      if (n !== null) warningsByCode[k] = n;
    }
  }
  // A gate that says "passed" while listing violations is internally inconsistent: reject it.
  const violationFindings = findings.filter((f) => f.severity === "violation").length;
  if (body.passed && violationFindings > 0) return null;
  // The headline counts must agree with the verdict and the listed findings, or the Activity
  // panel and the Coverage scorecard could read the same body two ways.
  if (body.passed && violationCount! > 0) return null;
  if (!body.passed && violationCount! === 0) return null;
  if (violationFindings > violationCount!) return null;
  return {
    passed: body.passed,
    checked: checked!,
    freeAvailable: freeAvailable!,
    disabledOk: disabledOk!,
    gatedUnverified: gatedUnverified!,
    violationCount: violationCount!,
    warningCount: warningCount!,
    warningsByCode,
    findings,
  };
}
