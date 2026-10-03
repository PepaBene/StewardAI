import type { HoldingSegments } from "@/lib/api/client";
import { isFiniteNumber } from "@/lib/charts/scale";
import type { DataState } from "@/lib/data-state/states";

import type {
  ActualPoint,
  ActualVsConsensus,
  CashWalk,
  ConsensusSeries,
  DebtSchedule,
  EstimateModel,
  FactRow,
  ForwardObs,
  LeaseFact,
  LineVersion,
  PeerMetricRow,
  PeerMetricsPayload,
  PeriodBasis,
  RevisionSlope,
  SegmentRowView,
  SegmentSet,
  SeriesOption,
  StateAndReason,
  StatementKind,
  StatementLine,
  StatementPeriod,
  StatementView,
  VersionMode,
} from "./types";

const DAY_MS = 86_400_000;

function days(from: string, to: string): number {
  return Math.round((Date.parse(to) - Date.parse(from)) / DAY_MS);
}

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** A date (YYYY-MM-DD) as the end-of-day instant `as_of` expects; null = the current view. The day is a UTC day. */
export function asOfParam(date: string | null): string | undefined {
  // The backend compares `known_at <= as_of`; .999999 keeps the whole last second of the day in.
  return date ? `${date}T23:59:59.999999Z` : undefined;
}

/** Today's date in the viewer's own time zone (YYYY-MM-DD), for the date picker's upper bound. */
export function localIsoDate(now: Date = new Date()): string {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/** Everything that changes what the financial sections fetch: a change of any part remounts them. */
export function sectionKey(ticker: string, asOf: string | null, reconciledOnly: boolean, refresh: number): string {
  return `${ticker}:${asOf ?? "latest"}:${reconciledOnly ? "reconciled" : "all"}:${refresh}`;
}

const WEB_URL = /^https?:\/\//i;
/** A link target only when it is an http(s) URL; anything else (javascript:, data:) is never an href. */
export function safeHref(url: string | null | undefined): string | null {
  return typeof url === "string" && WEB_URL.test(url.trim()) ? url.trim() : null;
}

// ---------------------------------------------------------------------------------------
// Fact selection
// ---------------------------------------------------------------------------------------

/**
 * Concepts the backend maps to one line item, best first. Rows of one filing share `known_at`
 * and `revision`, so without a fixed preference the winner would be whichever row was inserted
 * last. A concept not listed ranks after every listed one.
 */
const CONCEPT_PREFERENCE: Readonly<Record<string, readonly string[]>> = {
  net_income: ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
  capex: ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
  depreciation_amortization: ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
  cash_from_operations: ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
};

function localConcept(concept: string): string {
  return concept.split(":").pop() as string;
}

export function conceptRank(lineItem: string, concept: string): number {
  const i = (CONCEPT_PREFERENCE[lineItem] ?? []).indexOf(localConcept(concept));
  return i === -1 ? 100 : i;
}

/** The newest fact of the single best-ranked concept: facts of different concepts are never compared by recency. */
export function newestPreferred(lineItem: string, facts: readonly FactRow[]): FactRow | null {
  if (facts.length === 0) return null;
  const concepts = [...new Set(facts.map((f) => f.concept))].sort((a, b) => conceptRank(lineItem, a) - conceptRank(lineItem, b) || a.localeCompare(b));
  return newest(facts.filter((f) => f.concept === concepts[0]));
}

/** A consolidated, numeric statement fact: not a breakdown, not a text block, has a value. */
export function isStatementFact(f: FactRow): boolean {
  return !f.dimensional && !f.textBlock && f.lineItem !== null && f.value !== null;
}

/** Facts a reconciliation check failed are never used in a figure (they are flagged and counted). */
export function usable(f: FactRow): boolean {
  return f.reconciliation !== "failed";
}

export type PeriodClass = "annual" | "quarter" | "other" | "instant";

export function periodClass(f: Pick<FactRow, "periodStart" | "periodEnd">): PeriodClass {
  if (!f.periodStart) return "instant";
  const d = days(f.periodStart, f.periodEnd);
  if (d >= 330 && d <= 380) return "annual";
  if (d >= 80 && d <= 100) return "quarter";
  return "other";
}

/** The most recent version of a set of facts of one value: latest `known_at`, then revision, then id. */
export function newest(facts: readonly FactRow[]): FactRow | null {
  let best: FactRow | null = null;
  for (const f of facts) {
    if (!best || f.knownAt > best.knownAt || (f.knownAt === best.knownAt && ((f.revision ?? 0) > (best.revision ?? 0) || ((f.revision ?? 0) === (best.revision ?? 0) && f.id > best.id)))) best = f;
  }
  return best;
}

// ---------------------------------------------------------------------------------------
// As-filed statements: versions and point-in-time
// ---------------------------------------------------------------------------------------

/** Concepts shown elsewhere (debt ladder, leases): not statement lines of the explorer. */
const NOT_STATEMENT_LINES = /^(debt_maturity_|lease_payments_due_)/;

function humanize(lineItem: string): string {
  const text = lineItem.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function toVersions(facts: readonly FactRow[]): LineVersion[] {
  // One version per filing (accession); several rows of one accession are one filed value.
  const byFiling = new Map<string, FactRow>();
  for (const f of facts) {
    const key = f.accession ?? `fact:${f.id}`;
    const prev = byFiling.get(key);
    if (!prev || newest([prev, f]) === f) byFiling.set(key, f);
  }
  const sorted = [...byFiling.values()].sort((a, b) => (a.knownAt === b.knownAt ? (a.revision ?? 0) - (b.revision ?? 0) || a.id - b.id : a.knownAt < b.knownAt ? -1 : 1));
  return sorted.map((f, i) => ({
    version: i + 1,
    value: f.value as number,
    knownAt: f.knownAt,
    accession: f.accession,
    form: f.form,
    restates: f.amendsFactId !== null || f.amendsAccession !== null || f.isAmendment,
    reconciliation: f.reconciliation,
    concept: f.concept,
    sourceUrl: f.sourceUrl,
  }));
}

/** The version a mode selects for a period, or null when none was filed. */
export function versionAt(period: StatementPeriod, mode: VersionMode): LineVersion | null {
  const versions = [...period.versions].sort((a, b) => a.version - b.version);
  if (versions.length === 0) return null;
  return mode.kind === "original" ? versions[0] : versions[versions.length - 1];
}

export type LinePoint = { period: string; end: string; value: number | null; restated: boolean; version: number | null };

/** The line's values under a mode. A period with nothing filed is null (never zero). */
export function linePoints(line: StatementLine, mode: VersionMode): LinePoint[] {
  return line.periods.map((p) => {
    const v = versionAt(p, mode);
    return { period: p.label, end: p.end, value: v ? v.value : null, restated: !!v && v.restates, version: v ? v.version : null };
  });
}

/** Periods whose latest version has a different value than the first filing: what a restatement changed. */
export function restatements(line: StatementLine): { period: string; original: number; latest: number; delta: number; filedAt: string }[] {
  const out: { period: string; original: number; latest: number; delta: number; filedAt: string }[] = [];
  for (const p of line.periods) {
    const first = versionAt(p, { kind: "original" });
    const last = versionAt(p, { kind: "latest" });
    if (first && last && first.version !== last.version && first.value !== last.value) {
      out.push({ period: p.label, original: first.value, latest: last.value, delta: last.value - first.value, filedAt: last.knownAt });
    }
  }
  return out;
}

/** Every filing date among the versions, newest first. */
export function knownDates(lines: readonly StatementLine[]): string[] {
  const dates = new Set<string>();
  for (const l of lines) for (const p of l.periods) for (const v of p.versions) dates.add(v.knownAt.slice(0, 10));
  return [...dates].sort().reverse();
}

/** The period ends of a basis present in a set of facts (flow facts decide; instants join by end date). */
function periodEndsOf(facts: readonly FactRow[], basis: PeriodBasis): Set<string> {
  const wanted: PeriodClass = basis === "annual" ? "annual" : "quarter";
  const ends = new Set<string>();
  for (const f of facts) if (isStatementFact(f) && periodClass(f) === wanted) ends.add(f.periodEnd);
  // A fiscal year end is also the fourth quarter's balance date: a 10-K carries no 3-month flow,
  // so without this the year-end balance sheet would have no column in the quarterly view.
  if (basis === "quarter" && ends.size > 0) for (const f of facts) if (isStatementFact(f) && periodClass(f) === "annual") ends.add(f.periodEnd);
  return ends;
}

function addDays(date: string, n: number): string {
  return new Date(Date.parse(date) + n * DAY_MS).toISOString().slice(0, 10);
}

function fiscalYearOf(fp: string | null): string | null {
  const m = fp ? /^FY(\d{4})/.exec(fp) : null;
  return m ? m[1] : null;
}

/**
 * Discrete quarters computed from cumulative filings: 10-Q cash-flow facts are year to date
 * (6 and 9 months) and a 10-K carries the 12 months, so Q2 = 6M - 3M, Q3 = 9M - 6M, Q4 = 12M - 9M.
 * Only where both operands are filed with the same concept and unit, and never where the
 * quarter itself is filed. Each result has form "derived", unreconciled, with the
 * arithmetic in `checkNote`.
 */
export function deriveDiscreteQuarters(facts: readonly FactRow[]): FactRow[] {
  const usableFlows = facts.filter((f) => isStatementFact(f) && usable(f) && f.periodStart !== null);
  const groups = new Map<string, FactRow[]>();
  for (const f of usableFlows) {
    const key = [f.statement, f.lineItem, f.concept, f.unit ?? "", f.periodStart].join("|");
    groups.set(key, [...(groups.get(key) ?? []), f]);
  }
  const filedQuarters = new Set(usableFlows.filter((f) => periodClass(f) === "quarter").map((f) => `${f.statement}|${f.lineItem}|${f.concept}|${f.unit ?? ""}|${f.periodEnd}`));
  const out: FactRow[] = [];
  for (const group of groups.values()) {
    const cum = (lo: number, hi: number) => newest(group.filter((f) => { const d = days(f.periodStart as string, f.periodEnd); return d >= lo && d <= hi; }));
    const steps: { n: number; cur: FactRow | null; prev: FactRow | null }[] = [
      { n: 2, cur: cum(160, 200), prev: cum(80, 100) },
      { n: 3, cur: cum(250, 290), prev: cum(160, 200) },
      { n: 4, cur: cum(330, 380), prev: cum(250, 290) },
    ];
    for (const { n, cur, prev } of steps) {
      if (!cur || !prev || cur.value === null || prev.value === null) continue;
      if (Date.parse(prev.periodEnd) >= Date.parse(cur.periodEnd)) continue;
      const key = `${cur.statement}|${cur.lineItem}|${cur.concept}|${cur.unit ?? ""}|${cur.periodEnd}`;
      if (filedQuarters.has(key)) continue;
      const year = fiscalYearOf(cur.fiscalPeriod);
      out.push({
        ...cur,
        id: -(cur.id * 10 + n),
        periodStart: addDays(prev.periodEnd, 1),
        fiscalPeriod: year ? `FY${year}Q${n}` : null,
        value: cur.value - prev.value,
        knownAt: cur.knownAt > prev.knownAt ? cur.knownAt : prev.knownAt,
        revision: 0,
        amendsFactId: null,
        amendsAccession: null,
        isAmendment: false,
        accession: null,
        form: "derived",
        reconciliation: cur.reconciliation === "reconciled" && prev.reconciliation === "reconciled" ? "reconciled" : "unchecked",
        checkNote: `Derived: ${cur.periodStart} to ${cur.periodEnd} year-to-date minus ${prev.periodStart} to ${prev.periodEnd} year-to-date`,
      });
    }
  }
  return out;
}

/**
 * One statement as lines across period ends. `allFacts` are the facts of every statement
 * fetched (so an instant balance-sheet fact joins the flow period that ends on its date).
 * Failed-reconciliation facts are counted, never drawn. A line takes the one concept with
 * the most periods, so two tags of one line item are never mixed within a series.
 */
export function buildStatement(rawFacts: readonly FactRow[], kind: StatementKind, basis: PeriodBasis, maxPeriods = 8): StatementView {
  const derivedFacts = basis === "quarter" ? deriveDiscreteQuarters(rawFacts.filter((f) => f.statement === kind)) : [];
  const allFacts = derivedFacts.length > 0 ? [...rawFacts, ...derivedFacts] : rawFacts;
  const ends = periodEndsOf(allFacts, basis);
  const wanted: PeriodClass = basis === "annual" ? "annual" : "quarter";
  const inKind = allFacts.filter((f) => isStatementFact(f) && f.statement === kind && !NOT_STATEMENT_LINES.test(f.lineItem as string));
  const inBasis = inKind.filter((f) => {
    const c = periodClass(f);
    return c === wanted || (c === "instant" && ends.has(f.periodEnd));
  });
  const derivedUsed = inBasis.filter((f) => f.form === "derived" && usable(f)).length;
  const counts = { reconciled: 0, unchecked: 0, failed: 0, total: 0 };
  for (const f of inBasis) {
    counts.total += 1;
    counts[f.reconciliation] += 1;
  }
  const used = inBasis.filter(usable);

  const byLine = new Map<string, FactRow[]>();
  for (const f of used) byLine.set(f.lineItem as string, [...(byLine.get(f.lineItem as string) ?? []), f]);

  const allEnds = [...new Set(used.map((f) => f.periodEnd))].sort().slice(-maxPeriods);
  const labels = new Map<string, string>();
  for (const end of allEnds) {
    const fp = used.filter((f) => f.periodEnd === end && f.fiscalPeriod).map((f) => f.fiscalPeriod as string);
    const tally = new Map<string, number>();
    for (const l of fp) tally.set(l, (tally.get(l) ?? 0) + 1);
    const top = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
    labels.set(end, top ? top[0] : end);
  }

  const lines: StatementLine[] = [];
  for (const [lineItem, facts] of byLine) {
    // Unit: the one with most facts (a USD line is never mixed with a EUR line).
    const unitCount = new Map<string, number>();
    for (const f of facts) unitCount.set(f.unit ?? "", (unitCount.get(f.unit ?? "") ?? 0) + 1);
    const unit = [...unitCount.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
    const sameUnit = facts.filter((f) => (f.unit ?? "") === unit && allEnds.includes(f.periodEnd));
    if (sameUnit.length === 0) continue;
    // Each period takes the best-ranked concept filed for it (fixed preference, then the concept
    // with the most periods), so a tag switch does not blank the periods filed under the other tag.
    const conceptEnds = new Map<string, Set<string>>();
    for (const f of sameUnit) conceptEnds.set(f.concept, (conceptEnds.get(f.concept) ?? new Set()).add(f.periodEnd));
    const order = [...conceptEnds.entries()].sort((a, b) => conceptRank(lineItem, a[0]) - conceptRank(lineItem, b[0]) || b[1].size - a[1].size || a[0].localeCompare(b[0])).map(([c]) => c);
    const chosenBy = new Map<string, string>();
    for (const end of allEnds) {
      const c = order.find((cc) => conceptEnds.get(cc)?.has(end));
      if (c) chosenBy.set(end, c);
    }
    const periods: StatementPeriod[] = allEnds.map((end) => {
      const c = chosenBy.get(end) ?? null;
      return {
        end,
        label: labels.get(end) ?? end,
        concept: c,
        versions: c ? toVersions(sameUnit.filter((f) => f.periodEnd === end && f.concept === c)) : [],
      };
    });
    const used_concepts = [...new Set([...chosenBy.values()])];
    const tally = new Map<string, number>();
    for (const c of chosenBy.values()) tally.set(c, (tally.get(c) ?? 0) + 1);
    const concept = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
    const chosen = sameUnit.filter((f) => chosenBy.get(f.periodEnd) === f.concept);
    lines.push({
      id: lineItem,
      label: chosen.find((f) => f.label)?.label ?? humanize(lineItem),
      statement: kind,
      unit: unit || "units",
      concept,
      concepts: used_concepts,
      periods,
    });
  }
  lines.sort((a, b) => a.id.localeCompare(b.id));
  const notes: string[] = [];
  if (derivedUsed > 0) notes.push(`${derivedUsed} quarterly figure${derivedUsed === 1 ? " is" : "s are"} derived, not filed as such: a year-to-date figure minus the previous year-to-date.`);
  if (basis === "quarter" && kind === "cash_flow" && lines.length > 0) {
    if (derivedFacts.length === 0) notes.push("Cash-flow statements in quarterly filings are cumulative from the start of the fiscal year; quarters that cannot be derived from filed year-to-date figures are shown as not filed, and the fourth quarter is only in the annual filing.");
  }
  if (basis === "quarter" && kind === "balance_sheet" && lines.length > 0) notes.push("A fiscal-year-end balance sheet is shown as the fourth quarter's balance (the annual filing carries it).");
  return { kind, basis, lines, periods: allEnds.map((end) => ({ end, label: labels.get(end) ?? end })), counts, derivedCount: derivedUsed, notes };
}

/** The data state a statement view wears, from its reconciliation counts. */
export function statementState(view: StatementView, droppedRows = 0): StateAndReason {
  const { counts } = view;
  if (counts.total === 0 || view.lines.length === 0) {
    return { state: "not_covered", reason: counts.failed > 0 ? `All ${counts.failed} stored facts for this view failed reconciliation and are not drawn.` : "No filed statement facts are stored for this statement and period type." };
  }
  if (counts.failed > 0) {
    return { state: "disputed", reason: `${counts.failed} of ${counts.total} facts failed a reconciliation check and are left out; the figures drawn are the ones that did not fail.` };
  }
  if (counts.unchecked > 0 || droppedRows > 0) {
    return {
      state: "partial",
      reason: `${counts.unchecked} of ${counts.total} facts were not covered by any reconciliation check (kept, flagged)${droppedRows > 0 ? `; ${droppedRows} malformed rows were dropped` : ""}.`,
    };
  }
  return { state: "verified", reason: null };
}

// ---------------------------------------------------------------------------------------
// Cash-to-earnings walk, from filed facts only
// ---------------------------------------------------------------------------------------

const ADJUSTMENT_ITEMS: readonly { id: string; label: string }[] = [
  { id: "depreciation_amortization", label: "Depreciation and amortisation" },
  { id: "share_based_compensation", label: "Share-based compensation" },
];

function factsOf(facts: readonly FactRow[], lineItem: string): FactRow[] {
  return facts.filter((f) => isStatementFact(f) && f.lineItem === lineItem && usable(f));
}

function pick(facts: readonly FactRow[], lineItem: string, start: string | null, end: string, unit: string | null): FactRow | null {
  return newestPreferred(lineItem, factsOf(facts, lineItem).filter((f) => f.periodStart === start && f.periodEnd === end && f.unit === unit));
}

export type CashWalkResult = { walk: CashWalk; reason?: undefined } | { walk: null; reason: string };

/**
 * The latest full fiscal year for which net income and cash from operations are both filed
 * in one unit. Only filed lines are used; free cash flow is derived (CFO − capex) and
 * labelled so. Facts a reconciliation check failed are never used.
 */
export function cashWalkFromFacts(facts: readonly FactRow[]): CashWalkResult {
  const cfoFacts = factsOf(facts, "cash_from_operations").filter((f) => periodClass(f) === "annual");
  if (cfoFacts.length === 0) {
    const failed = facts.some((f) => f.lineItem === "cash_from_operations" && f.reconciliation === "failed");
    return { walk: null, reason: failed ? "Cash from operations failed a reconciliation check and is not used." : "No filed annual cash-from-operations fact is stored." };
  }
  const candidates = [...new Map(cfoFacts.map((f) => [`${f.periodStart}|${f.periodEnd}|${f.unit}`, f])).values()].sort((a, b) => (a.periodEnd < b.periodEnd ? 1 : -1));
  for (const c of candidates) {
    const ni = pick(facts, "net_income", c.periodStart, c.periodEnd, c.unit);
    if (!ni) continue;
    const cfo = newestPreferred("cash_from_operations", cfoFacts.filter((f) => f.periodStart === c.periodStart && f.periodEnd === c.periodEnd && f.unit === c.unit)) as FactRow;
    const adjustments: { id: string; label: string; value: number }[] = [];
    const used: FactRow[] = [ni, cfo];
    for (const a of ADJUSTMENT_ITEMS) {
      const f = pick(facts, a.id, c.periodStart, c.periodEnd, c.unit);
      if (f) {
        adjustments.push({ id: a.id, label: a.label, value: f.value as number });
        used.push(f);
      }
    }
    const capexFact = pick(facts, "capex", c.periodStart, c.periodEnd, c.unit);
    if (capexFact) used.push(capexFact);
    const accessions = [...new Set(used.map((f) => f.accession).filter((a): a is string => !!a))];
    return {
      walk: {
        label: cfo.fiscalPeriod ?? `year to ${c.periodEnd}`,
        periodStart: c.periodStart as string,
        periodEnd: c.periodEnd,
        currency: cfo.currency ?? cfo.unit ?? "",
        netIncome: ni.value as number,
        adjustments,
        cfo: cfo.value as number,
        capex: capexFact ? Math.abs(capexFact.value as number) : null,
        knownAt: used.map((f) => f.knownAt).sort().pop() as string,
        accessions,
        source: cfo.source,
        sourceUrl: cfo.sourceUrl,
      },
    };
  }
  return { walk: null, reason: "No fiscal year has both net income and cash from operations filed in the same unit." };
}

export type WalkStep = {
  label: string;
  /** The change this step contributes (for totals, the running level). */
  value: number;
  kind: "start" | "delta" | "subtotal" | "end";
  from: number;
  to: number;
};

export type WalkResult = {
  steps: WalkStep[];
  /** Net income plus the filed adjustments. */
  derivedCfo: number;
  /** derivedCfo − filed CFO: what the stored lines do not explain (working capital, other items). */
  cfoGap: number;
  /** Filed CFO less filed capex; null without capex. */
  fcf: number | null;
  reconciles: boolean;
  fcfConversion: number | null;
  cfoConversion: number | null;
};

/**
 * Net income → filed adjustments → operating cash flow → free cash flow. The walk
 * `reconciles` only when net income plus the filed adjustments equals filed operating cash
 * flow within `tolerance` of it; otherwise the unexplained gap is returned and no waterfall
 * may be drawn from it.
 */
export function cashWalk(w: CashWalk, tolerance = 0.005): WalkResult {
  const steps: WalkStep[] = [];
  let level = w.netIncome;
  steps.push({ label: "Net income", value: level, kind: "start", from: 0, to: level });
  for (const a of w.adjustments) {
    const from = level;
    level += a.value;
    steps.push({ label: a.label, value: a.value, kind: "delta", from, to: level });
  }
  const derivedCfo = level;
  const cfoGap = derivedCfo - w.cfo;
  const reconciles = Math.abs(cfoGap) / Math.max(Math.abs(w.cfo), 1) <= tolerance;
  steps.push({ label: "Operating cash flow", value: w.cfo, kind: "subtotal", from: 0, to: w.cfo });
  let fcf: number | null = null;
  if (w.capex !== null) {
    fcf = w.cfo - Math.abs(w.capex);
    steps.push({ label: "Capital expenditure", value: -Math.abs(w.capex), kind: "delta", from: w.cfo, to: fcf });
    steps.push({ label: "Free cash flow (derived)", value: fcf, kind: "end", from: 0, to: fcf });
  }
  return {
    steps,
    derivedCfo,
    cfoGap,
    fcf,
    reconciles,
    fcfConversion: fcf !== null && w.netIncome > 0 ? fcf / w.netIncome : null,
    cfoConversion: w.netIncome > 0 ? w.cfo / w.netIncome : null,
  };
}

// ---------------------------------------------------------------------------------------
// Debt maturities, leases
// ---------------------------------------------------------------------------------------

const MATURITY_ITEMS: readonly { id: string; label: string }[] = [
  { id: "debt_maturity_remainder", label: "Rest of fiscal year" },
  { id: "debt_maturity_y1", label: "Next 12 months" },
  { id: "debt_maturity_y2", label: "Year 2" },
  { id: "debt_maturity_y3", label: "Year 3" },
  { id: "debt_maturity_y4", label: "Year 4" },
  { id: "debt_maturity_y5", label: "Year 5" },
  { id: "debt_maturity_after_y5", label: "After year 5" },
];

const LEASE_ITEMS: readonly { id: string; label: string }[] = [
  { id: "lease_liability", label: "Lease liability" },
  { id: "lease_liability_current", label: "Lease liability, current" },
  { id: "lease_liability_noncurrent", label: "Lease liability, non-current" },
  { id: "right_of_use_asset", label: "Right-of-use asset" },
  { id: "lease_payments_due_y1", label: "Lease payments due, next 12 months" },
  { id: "lease_payments_due_y2", label: "Lease payments due, year 2" },
  { id: "lease_payments_due_after_y5", label: "Lease payments due, after year 5" },
];

/** The debt maturity ladder of the latest balance date that has one, from filed note facts. */
export function debtFromFacts(facts: readonly FactRow[]): DebtSchedule | null {
  const ids = new Set(MATURITY_ITEMS.map((m) => m.id));
  const mat = facts.filter((f) => isStatementFact(f) && usable(f) && ids.has(f.lineItem as string));
  if (mat.length === 0) return null;
  const end = mat.map((f) => f.periodEnd).sort().pop() as string;
  const atEnd = mat.filter((f) => f.periodEnd === end);
  const unitCount = new Map<string, number>();
  for (const f of atEnd) unitCount.set(f.unit ?? "", (unitCount.get(f.unit ?? "") ?? 0) + 1);
  const unit = [...unitCount.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
  const tranches: { key: string; label: string; amount: number }[] = [];
  const used: FactRow[] = [];
  for (const m of MATURITY_ITEMS) {
    const f = newestPreferred(m.id, atEnd.filter((x) => x.lineItem === m.id && (x.unit ?? "") === unit));
    if (f) {
      tranches.push({ key: m.id, label: m.label, amount: f.value as number });
      used.push(f);
    }
  }
  const at = (item: string) => newestPreferred(item, factsOf(facts, item).filter((f) => f.periodEnd === end && f.periodStart === null && (f.unit ?? "") === unit));
  let total = at("long_term_debt_total") ?? at("borrowings_total");
  let totalBasis: string | null = total ? total.concept : null;
  let reportedTotal = total ? (total.value as number) : null;
  if (!total) {
    const nc = at("long_term_debt_noncurrent") ?? at("borrowings_noncurrent");
    const c = at("long_term_debt_current") ?? at("borrowings_current");
    if (nc && c) {
      reportedTotal = (nc.value as number) + (c.value as number);
      totalBasis = `${nc.concept} + ${c.concept}`;
      total = nc;
    }
  }
  if (total) used.push(total);
  const leases: LeaseFact[] = [];
  for (const l of LEASE_ITEMS) {
    const cands = factsOf(facts, l.id).filter((x) => (x.unit ?? "") === unit && x.periodEnd <= end);
    const latestEnd = cands.map((x) => x.periodEnd).sort().pop();
    const f = latestEnd ? newestPreferred(l.id, cands.filter((x) => x.periodEnd === latestEnd)) : null;
    if (f) leases.push({ id: l.id, label: l.label, value: f.value as number, periodEnd: f.periodEnd });
  }
  const first = used[0];
  return {
    periodEnd: end,
    currency: first.currency ?? (unit || ""),
    tranches,
    reportedTotal,
    totalBasis,
    leases,
    knownAt: used.map((f) => f.knownAt).sort().pop() as string,
    accession: first.accession,
    source: first.source,
    sourceUrl: first.sourceUrl,
  };
}

export type DebtBreakdown = {
  classified: number;
  reportedTotal: number | null;
  /** reportedTotal − classified when positive beyond tolerance: the explicit unclassified remainder. */
  unclassified: number;
  unclassifiedShare: number | null;
  /** The maturity table exceeds reported debt beyond tolerance: a data error, not a negative remainder. */
  overClassified: boolean;
  /** Maturity table and balance-sheet debt agree within tolerance. */
  reconciles: boolean;
};

/**
 * Compares the maturity ladder with balance-sheet debt. Principal due can differ from the
 * carrying amount by discounts and issuance costs, so agreement is within `tolerance` of the
 * reported total; beyond it the remainder is shown as unclassified, never spread over years.
 */
export function debtBreakdown(d: DebtSchedule, tolerance = 0.02): DebtBreakdown {
  const classified = d.tranches.reduce((s, t) => s + (isFiniteNumber(t.amount) ? t.amount : 0), 0);
  const total = d.reportedTotal;
  if (total === null || total <= 0) {
    return { classified, reportedTotal: total, unclassified: 0, unclassifiedShare: null, overClassified: false, reconciles: false };
  }
  const gap = total - classified;
  const within = Math.abs(gap) / total <= tolerance;
  return {
    classified,
    reportedTotal: total,
    unclassified: gap > 0 && !within ? gap : 0,
    unclassifiedShare: gap > 0 && !within ? gap / total : null,
    overClassified: gap < 0 && !within,
    reconciles: within,
  };
}

// ---------------------------------------------------------------------------------------
// Segments
// ---------------------------------------------------------------------------------------

export type SegmentColorBy = "growth" | "margin";
export type SegmentAxisKey = "geographic" | "segment" | "product";

function localName(member: string): string {
  return member.split(":").pop()!.toLowerCase();
}

const SEGMENT_AXIS = /(^|:)StatementBusinessSegmentsAxis$/i;
const CONSOLIDATION_AXIS = /(^|:)ConsolidationItemsAxis$/i;

/** The business-segment member of a fact, when its context is a business-segment breakdown (alone, or with the consolidation axis of ASU 2023-07). */
function businessSegmentMember(f: FactRow): string | null {
  const seg = f.members.filter((m) => SEGMENT_AXIS.test(m.axis));
  if (seg.length !== 1) return null;
  if (f.members.some((m) => !SEGMENT_AXIS.test(m.axis) && !CONSOLIDATION_AXIS.test(m.axis))) return null;
  return seg[0].member;
}

/**
 * Segment operating profit facts (`statement=segment&line_item=operating_income`) matched to
 * the drawn rows by XBRL member, for the same period end and an annual duration.
 */
function profitFor(rows: readonly { member: string | null }[], periodEnd: string, facts: readonly FactRow[] | null, currency: string): Map<string, { value: number; concept: string }> {
  const out = new Map<string, { value: number; concept: string }>();
  if (!facts) return out;
  // Only the business-segment axis carries segment profit comparable to the segment revenue rows
  // (a product or geography member of the same name is a different breakdown); the profit must be
  // in the revenue's currency.
  const eligible = facts
    .filter((f) => f.statement === "segment" && f.lineItem === "operating_income" && f.value !== null && usable(f) && f.periodEnd === periodEnd && periodClass(f) === "annual" && (f.currency ?? f.unit ?? currency) === currency)
    .map((f) => ({ f, member: businessSegmentMember(f) }))
    .filter((x): x is { f: FactRow; member: string } => x.member !== null);
  for (const r of rows) {
    if (!r.member) continue;
    const exact = eligible.filter((x) => x.member === r.member);
    const pool = exact.length > 0 ? exact : eligible.filter((x) => localName(x.member) === localName(r.member as string));
    const f = newest(pool.map((x) => x.f));
    if (f) out.set(r.member, { value: f.value as number, concept: f.concept });
  }
  return out;
}

/**
 * The tiles of one breakdown of `/segments`: area = reported revenue (subtotal rows left out),
 * prior-year revenue from the same member of the period one year earlier, and, for the
 * business-segment breakdown, segment profit from filed segment facts.
 */
export function buildSegmentSet(data: HoldingSegments, axis: SegmentAxisKey, profitFacts: readonly FactRow[] | null): SegmentSet | null {
  const period = data.period;
  if (!period || !data.currency) return null;
  const rows = data[axis];
  const drawn = rows.filter((r) => !r.subtotal && (toNumber(r.value) ?? 0) > 0);
  if (drawn.length === 0) return null;
  const history = data.history?.[axis] ?? [];
  const priorPoint = history.find((h) => h.period.months === period.months && h.period.basis === period.basis && Math.abs(days(h.period.end, period.end) - 365) <= 20);
  const profit = axis === "segment" ? profitFor(drawn, period.end, profitFacts, data.currency) : new Map();
  const segments: SegmentRowView[] = drawn.map((r) => {
    const prior = priorPoint?.rows.find((p) => !p.subtotal && (r.member ? p.member === r.member : p.label === r.label));
    const p = r.member ? profit.get(r.member) : undefined;
    return {
      name: r.label,
      member: r.member,
      revenue: toNumber(r.value) as number,
      priorRevenue: prior ? toNumber(prior.value) : null,
      operatingProfit: p ? p.value : null,
      profitDefinition: p ? p.concept : null,
    };
  });
  segments.sort((a, b) => b.revenue - a.revenue);
  return {
    axis,
    period: period.label,
    periodEnd: period.end,
    currency: data.currency,
    segments,
    totalRevenue: toNumber(data.total_revenue),
    subtotalsLeftOut: rows.filter((r) => r.subtotal).length,
  };
}

export type SegmentAlignment = {
  growth: { aligned: boolean; reason: string | null };
  margin: { aligned: boolean; reason: string | null };
};

/**
 * Whether segments may be coloured by growth or margin on one scale. Growth needs a prior-year
 * figure for every segment; margin additionally needs a filed profit for each and one shared
 * profit definition. Otherwise the treemap keeps the area and drops the colour, with the reason.
 */
export function segmentAlignment(set: SegmentSet): SegmentAlignment {
  const segs = set.segments;
  const missingPrior = segs.filter((s) => !isFiniteNumber(s.priorRevenue) || s.priorRevenue <= 0);
  const growth = missingPrior.length
    ? { aligned: false, reason: `No comparable prior-year revenue for ${missingPrior.map((s) => s.name).join(", ")}.` }
    : { aligned: true, reason: null };
  let margin: { aligned: boolean; reason: string | null };
  if (set.axis !== "segment") margin = { aligned: false, reason: "Profit is reported by business segment only; regions and products carry no profit." };
  else {
    const noProfit = segs.filter((s) => !isFiniteNumber(s.operatingProfit));
    const defs = new Set(segs.map((s) => s.profitDefinition));
    margin = noProfit.length
      ? { aligned: false, reason: `No filed segment profit for ${noProfit.map((s) => s.name).join(", ")}.` }
      : defs.size > 1
        ? { aligned: false, reason: `Segment profit is tagged with different definitions (${[...defs].filter(Boolean).join(" vs ")}); margins are not comparable.` }
        : { aligned: true, reason: null };
  }
  return { growth, margin };
}

export type SegmentTile = { id: string; label: string; weight: number; change: number | null };

export function segmentTiles(set: SegmentSet, colorBy: SegmentColorBy): { items: SegmentTile[]; colored: boolean; reason: string | null; cap: number } {
  const align = segmentAlignment(set)[colorBy];
  const items: SegmentTile[] = set.segments.map((s) => ({
    id: s.member ?? s.name,
    label: s.name,
    weight: s.revenue,
    change: align.aligned ? (colorBy === "growth" ? s.revenue / (s.priorRevenue as number) - 1 : (s.operatingProfit as number) / s.revenue) : null,
  }));
  return { items, colored: align.aligned, reason: align.reason, cap: colorBy === "growth" ? 0.2 : 0.4 };
}

// ---------------------------------------------------------------------------------------
// Forward estimates
// ---------------------------------------------------------------------------------------

/** Consensus / guidance metric -> the statement line item that is its actual (as the backend maps it). */
export const ACTUAL_LINE_ITEM: Readonly<Record<string, string>> = {
  revenue: "revenue",
  eps_diluted: "eps_diluted",
  net_income: "net_income",
  operating_income: "operating_income",
  capex: "capex",
};

export function seriesId(s: { provider: string | null; currency: string | null; basis: string | null }): string {
  return `${s.provider ?? ""}|${s.currency ?? ""}|${s.basis ?? ""}`;
}

/** A vintage the provider stated as an ISO date; the backend allows free text (e.g. "Q1 2026 preview"). */
export function isDateVintage(v: string | null | undefined): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}(?![\d])/.test(v) && Number.isFinite(Date.parse(v.slice(0, 10)));
}

/** The date a snapshot is placed at: its stated ISO vintage, else the day it became known. Free-text vintages are labels only. */
export function vintageDate(o: Pick<ForwardObs, "vintage" | "knownAt">): string {
  return (isDateVintage(o.vintage) ? o.vintage : o.knownAt).slice(0, 10);
}

/**
 * Revision of the consensus mean between the first and last snapshot of ONE series (same
 * metric, period, provider, currency and basis, which is what `consensus_series` groups by).
 * Snapshots dated the same day count once (the later known one).
 */
export function revisionSlope(series: Pick<ConsensusSeries, "fiscalPeriod" | "vintages">): RevisionSlope | null {
  const byDate = new Map<string, ForwardObs>();
  for (const v of [...series.vintages].sort((a, b) => (a.knownAt < b.knownAt ? -1 : 1))) {
    if (v.value === null) continue;
    byDate.set(vintageDate(v), v);
  }
  const snaps = [...byDate.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  if (snaps.length < 2) return null;
  const [d0, a] = snaps[0];
  const [d1, b] = snaps[snaps.length - 1];
  const change = (b.value as number) - (a.value as number);
  const span = Math.max(1, days(d0, d1));
  const pct = a.value !== 0 ? change / Math.abs(a.value as number) : null;
  return {
    period: series.fiscalPeriod,
    from: { vintage: d0, mean: a.value as number, analystCount: a.analystCount },
    to: { vintage: d1, mean: b.value as number, analystCount: b.analystCount },
    snapshots: snaps.length,
    change,
    pct,
    days: span,
    perMonthPct: pct !== null ? (pct / span) * 30 : null,
  };
}

function latestPerKey(obs: readonly ForwardObs[]): ForwardObs[] {
  const best = new Map<string, ForwardObs>();
  for (const o of obs) {
    const key = [o.kind, o.provider ?? "", o.basis ?? "", o.currency ?? "", o.fiscalPeriod].join("|");
    const prev = best.get(key);
    if (!prev || o.knownAt > prev.knownAt || (o.knownAt === prev.knownAt && o.id > prev.id)) best.set(key, o);
  }
  return [...best.values()];
}

function pointValues(o: ForwardObs): number[] {
  return [o.value, o.low, o.high].filter((x): x is number => x !== null);
}

/** Chronological order key of a fiscal-period label (FY2025, FY2025Q3, FY2025H1, FY2025M9); NaN when it does not parse. */
export function fiscalPeriodKey(period: string): number {
  const m = /^FY(\d{4})(?:Q([1-4])|H([12])|M(\d{1,2}))?$/.exec(period);
  if (!m) return Number.NaN;
  const year = Number(m[1]);
  const within = m[2] ? Number(m[2]) * 25 : m[3] ? Number(m[3]) * 50 : m[4] ? (Number(m[4]) / 12) * 100 : 100;
  return year * 1000 + within;
}

export function comparePeriods(a: string, b: string): number {
  const ka = fiscalPeriodKey(a);
  const kb = fiscalPeriodKey(b);
  return Number.isFinite(ka) && Number.isFinite(kb) && ka !== kb ? ka - kb : a.localeCompare(b);
}

export type ActualGranularity = "annual" | "quarter" | "both";

/**
 * Actual values of a metric per fiscal period, newest filing per period, oldest period first
 * (by period end, not by label). `granularity` keeps a chart to one period length: an annual
 * figure and a quarterly figure are never points of one line.
 */
export function actualsFor(metric: string, facts: readonly FactRow[], granularity: ActualGranularity = "both"): { points: ActualPoint[]; currency: string | null } {
  const lineItem = ACTUAL_LINE_ITEM[metric];
  if (!lineItem) return { points: [], currency: null };
  const f = facts.filter((x) => {
    if (!isStatementFact(x) || !usable(x) || x.lineItem !== lineItem || !x.fiscalPeriod) return false;
    const c = periodClass(x);
    return granularity === "both" ? c === "annual" || c === "quarter" : c === granularity;
  });
  const units = new Map<string, number>();
  for (const x of f) units.set(x.unit ?? "", (units.get(x.unit ?? "") ?? 0) + 1);
  const unit = [...units.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  if (unit === undefined) return { points: [], currency: null };
  const same = f.filter((x) => (x.unit ?? "") === unit);
  const byPeriod = new Map<string, FactRow[]>();
  for (const x of same) byPeriod.set(x.fiscalPeriod as string, [...(byPeriod.get(x.fiscalPeriod as string) ?? []), x]);
  const points: ActualPoint[] = [];
  for (const [period, list] of byPeriod) {
    const n = newestPreferred(lineItem, list) as FactRow;
    points.push({ period, value: n.value as number, knownAt: n.knownAt, end: n.periodEnd, granularity: periodClass(n) === "annual" ? "annual" : "quarter" });
  }
  points.sort((a, b) => (a.end === b.end ? comparePeriods(a.period, b.period) : a.end < b.end ? -1 : 1));
  return { points, currency: same[0].currency };
}

/** The period length a set of forward figures is stated in: the more common of annual (FY2026) and quarterly (FY2026Q1). */
export function forwardGranularity(periods: readonly string[]): "annual" | "quarter" {
  const quarters = periods.filter((p) => /Q[1-4]$/.test(p)).length;
  const annual = periods.filter((p) => /^FY\d{4}$/.test(p)).length;
  return quarters > annual ? "quarter" : "annual";
}

/**
 * The fan-chart model for one metric. One consensus series (provider, currency, basis) is
 * plotted; guidance, scenario and calendar estimates are plotted only in the same currency.
 * Anything left out is listed with the reason. Actuals are drawn only if their currency
 * matches and their magnitude is plausible next to the estimates (a 100x gap means a unit or
 * scale mismatch, not a miss).
 */
export function buildEstimates(payload: { observations: readonly ForwardObs[]; series: readonly ConsensusSeries[] }, facts: readonly FactRow[], metric: string, chosenSeries: string | null = null): EstimateModel {
  const obs = payload.observations.filter((o) => o.metric === metric);
  const consensus = payload.series.filter((s) => s.metric === metric);
  const opts = new Map<string, SeriesOption>();
  for (const s of consensus) {
    const id = seriesId(s);
    const cur = opts.get(id);
    opts.set(id, cur ? { ...cur, periods: cur.periods + 1 } : { id, provider: s.provider, currency: s.currency, basis: s.basis, periods: 1 });
  }
  const seriesOptions = [...opts.values()].sort((a, b) => b.periods - a.periods || a.id.localeCompare(b.id));
  const seriesChoice = chosenSeries && opts.has(chosenSeries) ? chosenSeries : (seriesOptions[0]?.id ?? null);
  const chosen = seriesChoice ? opts.get(seriesChoice)! : null;

  let currency: string | null = chosen?.currency ?? null;
  if (!chosen) {
    const tally = new Map<string, number>();
    for (const o of obs) if (o.currency) tally.set(o.currency, (tally.get(o.currency) ?? 0) + 1);
    currency = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? null;
  }

  const unplotted: { obs: ForwardObs; reason: string }[] = [];
  const candidates: ForwardObs[] = [];
  for (const o of latestPerKey(obs)) {
    if (o.kind === "published_analyst_consensus") {
      if (!chosen || seriesId(o) !== chosen.id) {
        unplotted.push({ obs: o, reason: "A different provider, currency or basis than the plotted consensus series; series are never merged." });
        continue;
      }
    } else if (o.currency !== currency) {
      unplotted.push({ obs: o, reason: `Stated in ${o.currency ?? "no currency"}, the chart is in ${currency ?? "an unstated currency"}.` });
      continue;
    }
    if (pointValues(o).length === 0) {
      unplotted.push({ obs: o, reason: "The observation carries no value." });
      continue;
    }
    candidates.push(o);
  }
  const points = candidates.sort((a, b) => comparePeriods(a.fiscalPeriod, b.fiscalPeriod) || a.kind.localeCompare(b.kind));

  const forwardPeriods = [...new Set(points.map((p) => p.fiscalPeriod))].sort(comparePeriods);
  const act = actualsFor(metric, facts, forwardGranularity(forwardPeriods));
  let actuals: ActualPoint[] = act.points;
  let actualsNote: string | null = null;
  if (actuals.length > 0 && currency && act.currency && act.currency !== currency) {
    actualsNote = `Actuals are filed in ${act.currency}, estimates in ${currency}: not drawn on one axis.`;
    actuals = [];
  } else if (actuals.length > 0 && points.length > 0) {
    const est = points.flatMap(pointValues).map(Math.abs).filter((x) => x > 0);
    const last = Math.abs(actuals[actuals.length - 1].value);
    if (est.length > 0 && last > 0) {
      const mid = [...est].sort((a, b) => a - b)[Math.floor(est.length / 2)];
      const ratio = last / mid;
      if (ratio > 100 || ratio < 0.01) {
        actualsNote = "Actuals and estimates differ by more than 100x: a unit or scale mismatch, so actuals are not drawn on this axis.";
        actuals = [];
      }
    }
  }
  const lastActualPeriods = actuals.slice(-4).map((a) => a.period);
  const periods = [...new Set([...lastActualPeriods, ...forwardPeriods])].sort(comparePeriods);

  const revisions = chosen
    ? consensus
        .filter((s) => seriesId(s) === chosen.id)
        .sort((a, b) => comparePeriods(a.fiscalPeriod, b.fiscalPeriod))
        .map((s) => {
          const slope = revisionSlope(s);
          return { period: s.fiscalPeriod, slope, reason: slope ? null : s.vintages.length < 2 ? "One snapshot only: no revision to show." : "Snapshots carry no comparable consensus value." };
        })
    : [];
  const consensusPeriods = new Set(points.filter((p) => p.kind === "published_analyst_consensus").map((p) => p.fiscalPeriod));
  const consensusGaps = forwardPeriods.filter((p) => !consensusPeriods.has(p));
  return { metric, currency, seriesOptions, seriesId: seriesChoice, periods, actuals, actualsNote, points, unplotted, revisions, consensusGaps };
}

export function forwardMetrics(payload: { observations: readonly ForwardObs[] }): string[] {
  return [...new Set(payload.observations.map((o) => o.metric))].sort();
}

export type Surprise = {
  pct: number | null;
  position: "above_range" | "within_range" | "below_range" | "no_range";
};

/** Actual versus the consensus the API found before the report. No consensus yields null: nothing is invented. */
export function surprise(a: Pick<ActualVsConsensus, "actual" | "consensus"> & { surprisePct?: number | null }): Surprise {
  const actual = a.actual?.value ?? null;
  const c = a.consensus;
  if (actual === null || !c) return { pct: null, position: "no_range" };
  // (actual - expected) / |expected|, as the backend computes it: against a negative consensus a
  // narrower loss is a beat (positive), not a miss. The API's own figure wins when present.
  const computed = isFiniteNumber(c.value) && c.value !== 0 ? (actual - c.value) / Math.abs(c.value) : null;
  const pct = isFiniteNumber(a.surprisePct) ? a.surprisePct : computed;
  if (!isFiniteNumber(c.low) || !isFiniteNumber(c.high)) return { pct, position: "no_range" };
  return { pct, position: actual > c.high ? "above_range" : actual < c.low ? "below_range" : "within_range" };
}

/** The actual and the consensus mean differ by more than 100x: a unit or scale mismatch, not a surprise. */
export function avcScaleMismatch(a: Pick<ActualVsConsensus, "actual" | "consensus">): boolean {
  const actual = a.actual?.value;
  const mean = a.consensus?.value;
  if (!isFiniteNumber(actual) || !isFiniteNumber(mean) || actual === 0 || mean === 0) return false;
  const ratio = Math.abs(actual / mean);
  return ratio > 100 || ratio < 0.01;
}

/** Data state of an actual-vs-consensus answer. */
export function avcState(a: ActualVsConsensus): StateAndReason {
  switch (a.state) {
    case "available":
      if (avcScaleMismatch(a)) return { state: "disputed", reason: "The reported figure and the consensus differ by more than 100x: a unit or scale mismatch, so no surprise is computed." };
      return {
        state: a.actual?.reconciled ? "verified" : "partial",
        // The API reports `reconciled` as false both when no check ran and when a check failed.
        reason: a.actual?.reconciled ? null : "The reported figure is not confirmed by a reconciliation check (the API does not say whether none ran or one disagreed).",
      };
    case "no_consensus_before_report":
      return { state: "not_covered", reason: a.reason ?? "No consensus was known before the report." };
    case "currency_mismatch":
      return { state: "disputed", reason: a.reason ?? "Consensus and actual are in different currencies; no surprise is computed." };
    case "unsupported_metric":
      return { state: "not_covered", reason: "This metric has no matching reported line item." };
    case "no_actual":
      return { state: "not_covered", reason: a.reason ?? "No reported figure stored for this period." };
    default:
      return { state: "unavailable", reason: `Unrecognised answer state "${a.state}".` };
  }
}

// ---------------------------------------------------------------------------------------
// Peers
// ---------------------------------------------------------------------------------------

export const PEER_METRIC_META: Readonly<Record<string, { label: string; unit: "percent" | "ratio" }>> = {
  revenue_growth: { label: "Revenue growth", unit: "percent" },
  gross_margin: { label: "Gross margin", unit: "percent" },
  operating_margin: { label: "Operating margin", unit: "percent" },
  roic: { label: "ROIC", unit: "percent" },
  net_debt_to_equity: { label: "Net debt / equity", unit: "ratio" },
  free_cash_flow_conversion: { label: "FCF conversion", unit: "percent" },
};

export type PeerComparison = {
  id: string;
  label: string;
  unit: "percent" | "ratio";
  definition: string | null;
  holdingValue: number | null;
  holdingReason: string | null;
  mean: number | null;
  covered: number;
  /** Confirmed peers: the denominator. */
  total: number;
  included: readonly string[];
  excluded: readonly { ticker: string; reason: string }[];
  periodEnd: string | null;
  basis: string | null;
};

/**
 * One metric: the holding against the mean of its comparable confirmed peers, with the
 * covered/total count and the reason every other confirmed peer is out.
 */
export function peerComparison(p: PeerMetricsPayload, metricId: string): PeerComparison | null {
  const row: PeerMetricRow | undefined = p.metrics.find((m) => m.id === metricId);
  if (!row) return null;
  const meta = PEER_METRIC_META[metricId];
  // One confirmed peer can be stored once per source (suggested by a provider and re-added by the owner).
  const tickers = [...new Set(p.confirmedPeers.map((c) => c.peerTicker))];
  const total = tickers.length;
  const excludedAll = tickers.filter((t) => !row.included.includes(t));
  const wholly = new Map(p.excludedPeers.map((e) => [e.peer, e.reason]));
  return {
    id: metricId,
    label: meta?.label ?? metricId,
    unit: meta?.unit ?? "ratio",
    definition: p.formulas[metricId] ?? null,
    holdingValue: row.holdingValue,
    holdingReason: row.holdingReason,
    mean: row.mean,
    covered: row.included.length,
    total,
    included: row.included,
    excluded: excludedAll.map((t) => ({ ticker: t, reason: row.excludedReasons[t] ?? wholly.get(t) ?? "No value for this metric." })),
    periodEnd: row.periodEnd,
    basis: row.basis,
  };
}

/** Data state of the peer comparison: partial whenever a confirmed peer is out; not covered with none confirmed. */
export function peerState(p: PeerMetricsPayload, c: PeerComparison | null): StateAndReason {
  if (p.confirmedPeers.length === 0) return { state: "not_covered", reason: "No peer is confirmed for this holding. Peers are the owner's decision; suggested peers are never averaged." };
  if (!c) return { state: "not_covered", reason: "This metric is not in the response." };
  if (c.covered === 0) return { state: "not_covered", reason: "No confirmed peer has a comparable figure for this metric." };
  if (c.covered < c.total) return { state: "partial", reason: `${c.total - c.covered} of ${c.total} confirmed peers are excluded; see the reasons below.` };
  return { state: p.holdingPeriod?.reconciliation === "reconciled" ? "verified" : "partial", reason: p.holdingPeriod?.reconciliation === "reconciled" ? null : "Some inputs were not covered by a reconciliation check." };
}

/** The DataState for a plain list outcome (used for forward-data panels). */
export function forwardState(model: EstimateModel): StateAndReason {
  if (model.points.length === 0) return { state: "not_covered", reason: "No forward figure is stored for this metric. The app never fills one in." };
  const partial = model.consensusGaps.length > 0 || model.unplotted.length > 0 || model.actualsNote !== null;
  return { state: partial ? "partial" : "verified", reason: partial ? "Some figures are not plotted or some periods lack consensus; the reasons are listed below." : null } as { state: DataState; reason: string | null };
}
