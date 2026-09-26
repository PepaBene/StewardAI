import type { ComplianceRow, KpiRow, ProposedAction, ReviewBody, Source, StrategyConfig } from "@/lib/api/client";
import { buildCitationIndex, type CitationIndex } from "@/lib/sources/sources";

/*
 * Display rules for the monthly review (`GET /reviews/{id}`). Compliance and figures are
 * computed in code; the model only proposes actions, each resting on strategy rules or
 * thesis KPIs. The compliance table must show every strategy rule with pass / breach /
 * unknown: `missingRules` finds any rule of the strategy that the table leaves out, so the
 * page can say so instead of silently omitting it.
 */

export const REVIEWS_HREF = "/reviews";

export function reviewHref(id: number): string {
  return `/reviews/${id}`;
}

export type ComplianceStatus = ComplianceRow["status"];

export const COMPLIANCE_STATUS: Record<ComplianceStatus, { label: string; description: string }> = {
  pass: { label: "Pass", description: "Holds on the snapshot the review was judged on." },
  breach: { label: "Breach", description: "Broken on the snapshot the review was judged on." },
  unknown: { label: "Unknown", description: "Could not be judged (no snapshot, or the position is not held)." },
};

export const COMPLIANCE_KIND: Record<ComplianceRow["kind"], string> = {
  rule: "Strategy rule",
  hard_limit: "Hard limit",
  band: "Target band",
};

/** In-page anchors: a compliance row and a KPI row. */
export function complianceAnchor(id: string): string {
  return `compliance-${id.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export function kpiAnchor(ref: string): string {
  return `kpi-${ref.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

/** Breaches first, then unknown, then pass; rules before hard limits before bands; stable. */
export function sortCompliance(rows: readonly ComplianceRow[]): ComplianceRow[] {
  const status: Record<ComplianceStatus, number> = { breach: 0, unknown: 1, pass: 2 };
  const kind: Record<ComplianceRow["kind"], number> = { rule: 0, hard_limit: 1, band: 2 };
  return rows
    .map((row, i) => ({ row, i }))
    .sort((a, b) => status[a.row.status] - status[b.row.status] || kind[a.row.kind] - kind[b.row.kind] || a.i - b.i)
    .map((x) => x.row);
}

export function complianceCounts(rows: readonly ComplianceRow[]): Record<ComplianceStatus, number> {
  const out: Record<ComplianceStatus, number> = { pass: 0, breach: 0, unknown: 0 };
  for (const r of rows) out[r.status] += 1;
  return out;
}

/** Strategy rule ids the compliance table leaves out (empty when every rule is there). */
export function missingRules(
  strategy: Pick<StrategyConfig, "rules"> | null | undefined,
  rows: readonly Pick<ComplianceRow, "id" | "kind">[],
): { id: string; description: string }[] {
  const listed = new Set(rows.filter((r) => r.kind === "rule").map((r) => r.id));
  return (strategy?.rules ?? []).filter((r) => !listed.has(r.id)).map((r) => ({ id: r.id, description: r.description }));
}

/**
 * The strategy version the review was judged against: the numeric ref of its "strategy"
 * source (null when the review names none, e.g. no strategy was saved).
 */
export function strategyVersionOf(sources: readonly Pick<Source, "kind" | "ref">[]): number | null {
  const ref = sources.find((s) => s.kind === "strategy")?.ref;
  if (!ref || !/^\d+$/.test(ref)) return null;
  const n = Number(ref);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

/** "TICKER:kpi_id" -> its parts (the ticker may itself contain no colon). */
export function parseKpiRef(ref: string): { ticker: string; kpiId: string } | null {
  const at = ref.indexOf(":");
  if (at <= 0 || at === ref.length - 1) return null;
  return { ticker: ref.slice(0, at), kpiId: ref.slice(at + 1) };
}

export type ActionBasis = {
  rules: { id: string; row: ComplianceRow | null; href: string | null }[];
  kpis: { ref: string; row: KpiRow | null; ticker: string | null; href: string | null; holdingHref: string | null }[];
};

/** What a proposed action rests on, each linked to its compliance row or KPI row. */
export function actionBasis(action: ProposedAction, body: Pick<ReviewBody, "compliance" | "kpis">): ActionBasis {
  const rows = new Map(body.compliance.map((r) => [r.id, r]));
  const kpis = new Map(body.kpis.map((k) => [k.ref, k]));
  return {
    rules: action.rule_ids.map((id) => {
      const row = rows.get(id) ?? null;
      return { id, row, href: row ? `#${complianceAnchor(id)}` : null };
    }),
    kpis: action.kpis.map((ref) => {
      const row = kpis.get(ref) ?? null;
      const ticker = row?.ticker ?? parseKpiRef(ref)?.ticker ?? null;
      return {
        ref,
        row,
        ticker,
        href: row ? `#${kpiAnchor(ref)}` : null,
        holdingHref: ticker ? `/holdings/${encodeURIComponent(ticker)}` : null,
      };
    }),
  };
}

/** Every citation list in a review body: compliance rows, KPI rows and flags. */
export function reviewIndex(body: Pick<ReviewBody, "compliance" | "kpis" | "flags" | "sources">): CitationIndex {
  return buildCitationIndex(body.sources, [
    ...body.compliance.map((r) => r.source_ids ?? []),
    ...body.kpis.map((k) => k.source_ids ?? []),
    ...body.flags.map((f) => f.source_ids ?? []),
  ]);
}

/** "2026-09" -> "September 2026"; anything else as given. */
export function monthLabel(month: string, locale?: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  if (!m) return month;
  return new Date(Number(m[1]), Number(m[2]) - 1, 1).toLocaleDateString(locale, { month: "long", year: "numeric" });
}
