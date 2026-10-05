/**
 * View models for the Verification page, from `/verification/*` (all untyped). A gate that
 * could not be measured stays null ("Not measured"), coverage that does not exist is absent
 * ("No coverage"), and neither is ever turned into a zero.
 */
import { arr, isRecord, num, recs, str, titleCase } from "@/lib/parse-untyped";

const strings = (v: unknown): string[] => arr(v).filter((x): x is string => typeof x === "string");

// --- Zero-cost gate -----------------------------------------------------------------------

export type ZeroCostGate = {
  passed: boolean;
  checked: number | null;
  freeAvailable: number | null;
  disabledOk: number | null;
  gatedUnverified: number | null;
  violations: number | null;
  warnings: number | null;
  warningCodes: { code: string; count: number }[];
  findings: { provider: string; endpoint: string; severity: string; code: string; message: string }[];
};

export function parseZeroCost(payload: unknown): ZeroCostGate | null {
  if (!isRecord(payload)) return null;
  const codes = isRecord(payload.warnings_by_code) ? payload.warnings_by_code : {};
  return {
    passed: payload.passed === true,
    checked: num(payload.checked),
    freeAvailable: num(payload.free_available),
    disabledOk: num(payload.disabled_ok),
    gatedUnverified: num(payload.gated_unverified),
    violations: num(payload.violation_count),
    warnings: num(payload.warning_count),
    warningCodes: Object.entries(codes)
      .map(([code, n]) => ({ code, count: num(n) ?? 0 }))
      .sort((a, b) => b.count - a.count),
    findings: recs(payload.findings).map((f) => ({
      provider: str(f.provider) ?? "unknown",
      endpoint: str(f.endpoint) ?? "unknown",
      severity: str(f.severity) ?? "warning",
      code: str(f.code) ?? "",
      message: str(f.message) ?? "",
    })),
  };
}

// --- Domain gates -------------------------------------------------------------------------

export type DomainGate = { domain: string; allowed: boolean; version: string | null; samples: number | null; subjects: number | null; failures: number | null; reasons: string[] };
export type Conclusion = { name: string; domains: string[]; allowed: boolean };

export function parseDomainGates(payload: unknown): { domains: DomainGate[]; conclusions: Conclusion[] } | null {
  if (!isRecord(payload)) return null;
  const domains = isRecord(payload.domains) ? payload.domains : {};
  const conclusions = isRecord(payload.conclusions) ? payload.conclusions : {};
  return {
    domains: Object.entries(domains)
      .filter((e): e is [string, Record<string, unknown>] => isRecord(e[1]))
      .map(([domain, d]) => ({
        domain,
        allowed: d.allowed === true,
        version: str(d.threshold_version),
        samples: num(d.samples),
        subjects: num(d.distinct_subjects),
        failures: num(d.failures),
        reasons: strings(d.reasons),
      })),
    conclusions: Object.entries(conclusions)
      .filter((e): e is [string, Record<string, unknown>] => isRecord(e[1]))
      .map(([name, c]) => ({ name, domains: strings(c.domains), allowed: c.allowed === true })),
  };
}

// --- Signals ------------------------------------------------------------------------------

export type SignalGate = {
  name: string;
  publishable: boolean;
  sampleSize: number | null;
  /** Share of audited evidence rows that were correct; null when nothing was audited. */
  precision: number | null;
  pitChecked: number | null;
  pitViolations: number | null;
  auditedAt: string | null;
  reviewer: string | null;
  reasons: string[];
};

export function parseSignals(payload: unknown): SignalGate[] {
  if (!isRecord(payload)) return [];
  return Object.entries(payload)
    .filter((e): e is [string, Record<string, unknown>] => isRecord(e[1]))
    .map(([name, s]) => ({
      name,
      publishable: s.publishable === true,
      sampleSize: num(s.sample_size),
      precision: num(s.evidence_precision),
      pitChecked: num(s.pit_checked),
      pitViolations: num(s.pit_violations),
      auditedAt: str(s.audited_at),
      reviewer: str(s.reviewer),
      reasons: strings(s.reasons),
    }))
    .sort((a, b) => Number(a.publishable) - Number(b.publishable) || a.name.localeCompare(b.name));
}

export const signalLabel = titleCase;

// --- Measured coverage --------------------------------------------------------------------

export type CoverageRow = { jurisdiction: string; domain: string; metric: string; observations: number | null; unknownValues: number | null; sources: number | null; latestPeriod: string | null };
export type CoverageCell = "ok" | "partial" | "none";

export function parseCoverage(payload: unknown): CoverageRow[] {
  return recs(payload).map((r) => ({
    jurisdiction: str(r.jurisdiction) ?? "unknown",
    domain: str(r.domain) ?? "unknown",
    metric: str(r.metric) ?? "unknown",
    observations: num(r.observations),
    unknownValues: num(r.unknown_values),
    sources: num(r.sources),
    latestPeriod: str(r.latest_period),
  }));
}

/** Cell state: "ok" = observations with every value known, "partial" = some values unknown, "none" = no row (no coverage, not a zero). */
export function cellState(r: CoverageRow | undefined): CoverageCell {
  if (!r || !r.observations) return "none";
  return r.unknownValues && r.unknownValues > 0 ? "partial" : "ok";
}

export type CoverageGrid = {
  jurisdictions: string[];
  rows: { label: string; cells: CoverageCell[]; note: string }[];
  hiddenMetrics: number;
};

/** Metric x jurisdiction grid; metrics with the most coverage first, capped at `maxRows`. */
export function coverageGrid(rows: readonly CoverageRow[], maxRows = 40): CoverageGrid {
  const jurisdictions = [...new Set(rows.map((r) => r.jurisdiction))].sort();
  const byMetric = new Map<string, Map<string, CoverageRow>>();
  for (const r of rows) {
    const key = `${r.domain} / ${r.metric}`;
    const m = byMetric.get(key) ?? new Map<string, CoverageRow>();
    m.set(r.jurisdiction, r);
    byMetric.set(key, m);
  }
  const all = [...byMetric.entries()]
    .map(([label, m]) => ({
      label,
      cells: jurisdictions.map((j) => cellState(m.get(j))),
      note: `${m.size} of ${jurisdictions.length}`,
    }))
    .sort((a, b) => b.cells.filter((c) => c !== "none").length - a.cells.filter((c) => c !== "none").length || a.label.localeCompare(b.label));
  return { jurisdictions, rows: all.slice(0, maxRows), hiddenMetrics: Math.max(0, all.length - maxRows) };
}

// --- Shadow report ------------------------------------------------------------------------

export type ShadowProvider = {
  provider: string;
  recommendation: string | null;
  observations: number | null;
  valueAddedRatio: number | null;
  failureRate: number | null;
  quotaPressure: number | null;
  coverageRatio: number | null;
  reasons: string[];
};

export type ShadowReport = {
  generatedAt: string | null;
  windowDays: number | null;
  daysObserved: number | null;
  signOffReady: boolean;
  blockers: string[];
  providers: ShadowProvider[];
  notes: string[];
};

export function parseShadow(payload: unknown): ShadowReport | null {
  if (!isRecord(payload) || !isRecord(payload.report)) return null;
  const r = payload.report;
  return {
    generatedAt: str(payload.generated_at),
    windowDays: num(r.window_days),
    daysObserved: num(r.days_observed),
    signOffReady: r.sign_off_ready === true,
    blockers: arr(r.blockers).map((b) => (typeof b === "string" ? b : JSON.stringify(b))),
    providers: recs(r.providers).map((p) => ({
      provider: str(p.provider) ?? "unknown",
      recommendation: str(p.recommendation),
      observations: num(p.observations),
      valueAddedRatio: num(p.value_added_ratio),
      failureRate: num(p.failure_rate),
      quotaPressure: num(p.quota_pressure),
      coverageRatio: num(p.coverage_ratio),
      reasons: strings(p.recommendation_reasons),
    })),
    notes: strings(r.notes),
  };
}
