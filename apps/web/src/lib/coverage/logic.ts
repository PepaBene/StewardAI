import { DATA_STATES, type DataState } from "@/lib/data-state/states";
import { cellState } from "@/lib/source-coverage/logic";
import type { CoverageEntry, EntityKind, ZeroCostGate } from "@/lib/source-coverage/types";

import type { DataContextFigures, ShareFigure } from "./parse";
import { ATLAS_KINDS, type AtlasCell, type AtlasRow, type MeasuredCoverageRow, type ScoreItem } from "./types";

// ---------------------------------------------------------------------------------------
// Atlas matrix: dataset x entity kind, counts of ledger entries as the API returned them
// ---------------------------------------------------------------------------------------

function buildCell(dataset: string, kind: EntityKind, entries: readonly CoverageEntry[]): AtlasCell {
  const byState: Partial<Record<DataState, number>> = {};
  for (const e of entries) byState[e.state] = (byState[e.state] ?? 0) + 1;
  return {
    dataset,
    kind,
    entries: entries.length,
    available: entries.filter((e) => e.rawState === "available").length,
    byState,
    state: cellState(entries.map((e) => e.state)),
  };
}

/** One row per dataset (all providers merged), sorted by name; empty cells stay (entries 0 = unknown). */
export function buildAtlas(entries: readonly CoverageEntry[]): AtlasRow[] {
  const byDataset = new Map<string, CoverageEntry[]>();
  for (const e of entries) byDataset.set(e.dataset, [...(byDataset.get(e.dataset) ?? []), e]);
  return [...byDataset.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([dataset, list]) => ({
      dataset,
      providers: [...new Set(list.map((e) => e.provider))].sort(),
      cells: Object.fromEntries(
        ATLAS_KINDS.map((k) => [k, buildCell(dataset, k, list.filter((e) => e.entityKind === k))]),
      ) as Record<EntityKind, AtlasCell>,
    }));
}

/** The ledger rows behind a matrix cell, worst states first (gaps are what the owner drills for). */
export function drillEntries(entries: readonly CoverageEntry[], dataset: string, kind: EntityKind): CoverageEntry[] {
  const rank = (s: DataState) => (s === "verified" ? 99 : DATA_STATES.indexOf(s));
  return entries
    .filter((e) => e.dataset === dataset && e.entityKind === kind)
    .sort((a, b) => rank(a.state) - rank(b.state) || a.provider.localeCompare(b.provider) || a.entityKey.localeCompare(b.entityKey));
}

/** Every row, gaps first (verified rows last), capped so the table stays light. */
export function gapsFirst(entries: readonly CoverageEntry[], cap = 500): CoverageEntry[] {
  const rank = (e: CoverageEntry) => (e.state === "verified" ? 1 : 0);
  return [...entries].sort((a, b) => rank(a) - rank(b) || a.dataset.localeCompare(b.dataset) || a.entityKey.localeCompare(b.entityKey)).slice(0, cap);
}

/** Entries per DataState over a set, canonical order, zeros included. */
export function entryStateCounts(entries: readonly CoverageEntry[]): Record<DataState, number> {
  const counts = Object.fromEntries(DATA_STATES.map((s) => [s, 0])) as Record<DataState, number>;
  for (const e of entries) counts[e.state] += 1;
  return counts;
}

export function describeAtlasCell(cell: AtlasCell): string {
  return cell.entries === 0 ? "No ledger entry" : `${cell.available} of ${cell.entries} ledger ${cell.entries === 1 ? "entry" : "entries"} available`;
}

/** Display label of a summary group key; null keys are entries of another kind (not a "null" entity). */
export function summaryKeyLabel(groupBy: string, key: string | null): string {
  if (key !== null) return key;
  if (groupBy === "security") return "Not a security (geographies and other entities)";
  if (groupBy === "geography") return "Not a geography (securities and other entities)";
  if (groupBy === "period") return "No period recorded";
  return "No key";
}

// ---------------------------------------------------------------------------------------
// Release-target scorecard: every figure is taken from an API response
// ---------------------------------------------------------------------------------------

const pct = (f: number) => `${(f * 100).toFixed(1)}%`;

function shareText(s: ShareFigure): string {
  const ratio = s.ratio ?? (s.denominator > 0 ? s.numerator / s.denominator : null);
  return ratio === null ? `no denominator (${s.basis})` : `${pct(ratio)} of ${s.basis}`;
}

export const MISSING_ENDPOINT = "No API endpoint returns this figure";

export function identityScore(ctx: DataContextFigures | null, unresolvedHoldings: number | null): ScoreItem {
  const base = {
    id: "identity",
    title: "Identity",
    target: "100% of held positions have a reviewed issuer/security/listing path or a visible unresolved exception.",
    source: "GET /portfolio/data-context (identity_coverage), GET /reference/gaps",
    asOf: ctx?.snapshotDate ?? null,
  };
  const share = ctx?.identity ?? null;
  if (!ctx || !share || share.denominator <= 0) {
    return { ...base, status: "unavailable", measured: null, share: null, detail: ctx?.gaps.find((g) => g.domain === "portfolio")?.reason ?? "The API returned no identity share (no snapshot or zero portfolio value)." };
  }
  const ratio = share.ratio ?? share.numerator / share.denominator;
  const unresolved = unresolvedHoldings === null ? "the unresolved-holding list could not be loaded" : `${unresolvedHoldings} holding${unresolvedHoldings === 1 ? "" : "s"} listed as an unresolved exception`;
  return {
    ...base,
    status: ratio >= 1 && unresolvedHoldings === 0 ? "met" : "not_met",
    measured: shareText(share),
    share: Math.min(1, Math.max(0, ratio)),
    detail: `Counts positions with a confirmed issuer link; the API does not report a separate "reviewed" flag. ${unresolved}.`,
  };
}

export function provenanceScore(): ScoreItem {
  return {
    id: "provenance",
    title: "Provenance",
    target: "100% of displayed new facts and derived figures carry source, as-of date, method and access state.",
    status: "not_measured",
    measured: null,
    share: null,
    detail: "Each view shows provenance beside its figures, but the backend returns no count of displayed facts with and without it, so no percentage is stated.",
    source: `${MISSING_ENDPOINT} (provenance completeness).`,
    asOf: null,
  };
}

export type HeldPriceCount = { withClose: number; total: number; newestAsOf: string | null };

export function priceScore(count: HeldPriceCount | null): ScoreItem {
  const base = {
    id: "price",
    title: "Price coverage of held market value",
    target: "At least 95% of held market value has a usable latest close with explicit currency and adjustment basis; the rest is shown as an unknown bucket.",
    source: `GET /portfolio/holdings/prices (per-holding last close). ${MISSING_ENDPOINT} for the value-weighted share.`,
    asOf: count?.newestAsOf ?? null,
  };
  if (!count || count.total === 0) return { ...base, status: "unavailable", measured: null, share: null, detail: "No held positions or no price response, so nothing is measured." };
  return {
    ...base,
    status: "not_measured",
    measured: `${count.withClose} of ${count.total} held positions have a stored latest close (count of positions, not value-weighted)`,
    share: count.withClose / count.total,
    detail: "The 95% target is a share of market value. The API returns per-holding closes but no value-weighted share, so the target is not judged here.",
  };
}

export function returnWindowScore(ctx: DataContextFigures | null): ScoreItem {
  const base = {
    id: "analytics",
    title: "Portfolio analytics (return windows)",
    target: "A window labelled complete has reconciled flows and all material price/FX/corporate-action inputs; a partial window shows its missing-value share.",
    source: "GET /portfolio/data-context (adjusted_return_coverage)",
    asOf: ctx?.snapshotDate ?? null,
  };
  const bases = ctx?.returnBases ?? [];
  if (!ctx || bases.length === 0) return { ...base, status: "unavailable", measured: null, share: null, detail: "The API returned no return-basis coverage." };
  const parts = bases.map((b) => `${b.basis.replace(/_/g, " ")}: ${b.complete ? shareText(b.complete).replace(" of total portfolio value", "") : "unknown"}`);
  return { ...base, status: "reported", measured: `Complete share of portfolio value by basis: ${parts.join("; ")}`, share: null, detail: "Reported per adjustment basis; bases are never blended." };
}

export function costScore(gate: ZeroCostGate | null): ScoreItem {
  const base = {
    id: "cost",
    title: "Cost",
    target: "Actual spend remains $0; the usage ledger proves no paid endpoint or overage was used.",
    source: "GET /verification/zero-cost-gate",
    asOf: null,
  };
  if (!gate) return { ...base, status: "unavailable", measured: null, share: null, detail: "The zero-cost gate could not be loaded, so no pass or fail is shown." };
  return {
    ...base,
    status: gate.passed && gate.violationCount === 0 ? "met" : "not_met",
    measured: `${gate.checked} endpoints audited: ${gate.freeAvailable} free and available, ${gate.disabledOk} disabled, ${gate.gatedUnverified} gated and unverified; ${gate.violationCount} violations, ${gate.warningCount} warnings`,
    share: null,
    detail: "The gate audits the endpoint manifest and reports credits and findings. It does not report dollars.",
  };
}

export function globalReachScore(rows: readonly MeasuredCoverageRow[] | null): ScoreItem {
  const base = {
    id: "reach",
    title: "Global reach",
    target: "A dashboard by exchange, country, asset class and metric: tested symbols, valid records, history length and freshness. The 30-security audit sample is never extrapolated into a global percentage.",
    source: `GET /verification/coverage (jurisdiction x metric). ${MISSING_ENDPOINT} for exchange or asset class.`,
    asOf: null,
  };
  if (!rows) return { ...base, status: "unavailable", measured: null, share: null, detail: "The measured-coverage response was missing or malformed." };
  if (rows.length === 0) return { ...base, status: "unavailable", measured: null, share: null, detail: "No observation rows exist yet, so no jurisdiction is measured." };
  const jurisdictions = new Set(rows.map((r) => r.jurisdiction)).size;
  const metrics = new Set(rows.map((r) => `${r.domain}/${r.metric}`)).size;
  return {
    ...base,
    status: "reported",
    measured: `${jurisdictions} jurisdictions and ${metrics} metrics have stored observations`,
    share: null,
    detail: "These are measured rows only. A jurisdiction not listed has no stored observation, which says nothing about its global share.",
    asOf: rows.map((r) => r.newestKnownAt).filter((v): v is string => v !== null).sort().at(-1) ?? null,
  };
}

/** Rows filtered by a case-insensitive text match on jurisdiction, domain or metric; newest-known first. */
export function filterMeasured(rows: readonly MeasuredCoverageRow[], text: string): MeasuredCoverageRow[] {
  const q = text.trim().toLowerCase();
  return rows
    .filter((r) => q === "" || `${r.jurisdiction} ${r.domain} ${r.metric}`.toLowerCase().includes(q))
    .sort((a, b) => a.jurisdiction.localeCompare(b.jurisdiction) || a.domain.localeCompare(b.domain) || a.metric.localeCompare(b.metric));
}
