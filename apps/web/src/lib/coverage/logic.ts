import type { components } from "@/lib/api/schema";
import { DATA_STATES, toDataState, type DataState } from "@/lib/data-state/states";
import { cellState, gateVerdict } from "@/lib/source-coverage/logic";
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

/** Most rows the ledger table draws; the table says when more match. */
export const LEDGER_ROW_CAP = 500;

/**
 * The state the atlas footer shows for a set of entries: judged from the entries themselves
 * (never "verified" merely because rows exist), "not_covered" for none.
 */
export function atlasFooterState(entries: readonly CoverageEntry[]): DataState {
  return cellState(entries.map((e) => e.state)) ?? "not_covered";
}

/** Every row, gaps first (verified rows last), capped so the table stays light. */
export function gapsFirst(entries: readonly CoverageEntry[], cap = LEDGER_ROW_CAP): CoverageEntry[] {
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

type Schemas = components["schemas"];

const num0 = (v: number) => v.toLocaleString("en-GB", { maximumFractionDigits: 0 });

/**
 * Shown state of a price-coverage holding, through `toDataState` (the one adapter to the nine
 * states). The backend's own words are mapped to its vocabulary first: a usable close is
 * "available", no stored close is "not_covered" (a gap, not a zero price), a close without a
 * currency is "partial" (the number exists but cannot be used). Unknown strings stay unknown.
 */
export function priceHoldingState(state: string): DataState {
  const backend: Record<string, string> = { usable: "available", stale: "stale", no_price: "not_covered", basis_unknown: "partial" };
  return toDataState(Object.hasOwn(backend, state) ? backend[state] : state);
}

/** Held-value price coverage (`GET /portfolio/price-coverage`), against the plan's 95% target. */
export function priceScore(pc: Schemas["PriceCoverage"] | null): ScoreItem {
  const base = {
    id: "price",
    title: "Price coverage of held market value",
    target: "At least 95% of held market value has a usable latest close with explicit currency and adjustment basis; the rest is shown as an unknown bucket.",
    source: "GET /portfolio/price-coverage",
    asOf: pc?.snapshot_date ?? null,
  };
  if (!pc) return { ...base, status: "unavailable", measured: null, share: null, detail: "The price-coverage response could not be loaded, so nothing is measured." };
  if (pc.holdings_total === 0 || pc.usable_share === null) {
    return { ...base, status: "unavailable", measured: null, share: null, detail: "No held positions in the latest snapshot, so no share can be stated (unknown, not zero)." };
  }
  const cur = pc.base_currency ? ` ${pc.base_currency}` : "";
  const unknown = pc.unknown_share === null ? "" : `${pct(pc.unknown_share)} of held value (${num0(pc.unknown_value_base)}${cur}) is the unknown bucket: ${pc.holdings_stale} stale, ${pc.holdings_no_price} with no stored close, ${pc.holdings_basis_unknown} with no recorded currency`;
  return {
    ...base,
    status: pc.meets_target ? "met" : "not_met",
    measured: `${pct(pc.usable_share)} of held market value (${num0(pc.usable_value_base)} of ${num0(pc.denominator_base)}${cur}) has a usable latest close; ${unknown}`,
    share: Math.min(1, Math.max(0, pc.usable_share)),
    detail: `Usable means a stored adjusted close with a recorded currency, at most ${pc.max_stale_sessions} exchange sessions old. ${pc.holdings_usable} of ${pc.holdings_total} holdings are usable. The unknown bucket is never blended into a return.`,
  };
}

/** Reviewed identity path share (`GET /portfolio/identity-review`). */
export function identityScore(ir: Schemas["IdentityReview"] | null): ScoreItem {
  const base = {
    id: "identity",
    title: "Identity",
    target: "100% of held positions have a reviewed issuer/security/listing path or a visible unresolved exception.",
    source: "GET /portfolio/identity-review",
    asOf: ir?.snapshot_date ?? null,
  };
  if (!ir) return { ...base, status: "unavailable", measured: null, share: null, detail: "The identity-review response could not be loaded, so nothing is measured." };
  if (ir.held_positions === 0 || ir.reviewed_share === null) {
    return { ...base, status: "unavailable", measured: null, share: null, detail: "No held positions in the latest snapshot, so no share can be stated (unknown, not zero)." };
  }
  const accounted = ir.accounted_share === null ? "" : ` ${pct(ir.accounted_share)} of positions are either reviewed or a visible unresolved exception.`;
  return {
    ...base,
    status: ir.meets_target ? "met" : "not_met",
    measured: `${ir.reviewed} of ${ir.held_positions} held positions have a confirmed issuer/security/listing path (${ir.owner_reviewed} confirmed by you, ${ir.auto_confirmed} by the matcher's evidence rules); ${ir.unresolved_count} unresolved`,
    share: Math.min(1, Math.max(0, ir.reviewed_share)),
    detail: `The target is judged strictly: every held position needs a reviewed path.${accounted}`,
  };
}

/** Provenance completeness of the displayed fact tables (`GET /data/provenance-completeness`). */
export function provenanceScore(pv: Schemas["ProvenanceCompleteness"] | null): ScoreItem {
  const base = {
    id: "provenance",
    title: "Provenance",
    target: "100% of displayed new facts and derived figures carry source, as-of date, method and access state.",
    source: "GET /data/provenance-completeness",
    asOf: null,
  };
  if (!pv) return { ...base, status: "unavailable", measured: null, share: null, detail: "The provenance response could not be loaded, so nothing is measured." };
  const nonEmpty = pv.tables.filter((t) => t.rows > 0);
  if (nonEmpty.length === 0) return { ...base, status: "unavailable", measured: null, share: null, detail: "Every fact table is empty, so no completeness can be stated (unknown, not complete)." };
  const noColumn = nonEmpty.filter((t) => t.dimensions.some((d) => d.column === null)).map((t) => t.table);
  const parts = [
    pv.empty_tables.length ? `Empty tables (not judged): ${pv.empty_tables.join(", ")}.` : null,
    noColumn.length ? `Tables with no column for some dimension (counted as not carried): ${noColumn.join(", ")}.` : null,
  ].filter(Boolean);
  return {
    ...base,
    status: pv.meets_target ? "met" : "not_met",
    measured: `${pv.tables_meeting_target} of ${nonEmpty.length} non-empty fact tables carry source, as-of, known-at, method and access state on every row`,
    share: pv.tables_meeting_target / nonEmpty.length,
    detail: parts.length ? parts.join(" ") : "Every non-empty table carries every dimension on every row.",
  };
}

/** Global reach (`GET /data/reach`): reported, never judged and never turned into a global share. */
export function globalReachScore(r: Schemas["DataReach"] | null): ScoreItem {
  const base = {
    id: "reach",
    title: "Global reach",
    target: "A dashboard by exchange, country, asset class and metric: tested symbols, valid records, history length and freshness. The 30-security audit sample is never extrapolated into a global percentage.",
    source: "GET /data/reach",
    asOf: r?.as_of ?? null,
  };
  if (!r) return { ...base, status: "unavailable", measured: null, share: null, detail: "The reach response could not be loaded, so nothing is measured." };
  if (r.tested_symbols === 0) return { ...base, status: "unavailable", measured: null, share: null, detail: r.universe_note };
  const known = (rows: readonly Schemas["ReachRow"][], pick: (x: Schemas["ReachRow"]) => string | null) => new Set(rows.map(pick).filter((v): v is string => v !== null)).size;
  return {
    ...base,
    status: "reported",
    measured: `${r.tested_symbols} tested symbols (${r.held_tickers} held, ${r.watchlist_tickers} on the watchlist) across ${known(r.by_exchange, (x) => x.exchange_mic)} exchanges, ${known(r.by_country, (x) => x.country)} countries and ${known(r.by_asset_class, (x) => x.asset_class)} asset classes`,
    share: null,
    detail: r.universe_note,
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
  const verdict = gateVerdict(gate);
  if (verdict === "not_verified") {
    return {
      ...base,
      status: "unavailable",
      measured: "0 endpoints audited: the endpoint manifest is empty, so the gate verified nothing",
      share: null,
      detail: "Not verified. An audit of zero manifest endpoints cannot show that no paid endpoint was used; seed the endpoint manifest and reload.",
    };
  }
  return {
    ...base,
    status: verdict === "passed" ? "met" : "not_met",
    measured: `${gate.checked} endpoints audited: ${gate.freeAvailable} free and available, ${gate.disabledOk} disabled, ${gate.gatedUnverified} gated and unverified; ${gate.violationCount} violations, ${gate.warningCount} warnings`,
    share: null,
    detail: "The gate audits the endpoint manifest and reports credits and findings. It does not report dollars.",
  };
}

/** Rows filtered by a case-insensitive text match on jurisdiction, domain or metric; newest-known first. */
export function filterMeasured(rows: readonly MeasuredCoverageRow[], text: string): MeasuredCoverageRow[] {
  const q = text.trim().toLowerCase();
  return rows
    .filter((r) => q === "" || `${r.jurisdiction} ${r.domain} ${r.metric}`.toLowerCase().includes(q))
    .sort((a, b) => a.jurisdiction.localeCompare(b.jurisdiction) || a.domain.localeCompare(b.domain) || a.metric.localeCompare(b.metric));
}
