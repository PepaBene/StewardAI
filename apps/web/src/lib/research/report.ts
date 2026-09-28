import type {
  CitedSentence,
  ClaimCheckSummary,
  FactOut,
  KpiStatus,
  ResearchBody,
  ResearchProfilesConfig,
  ResearchSectionId,
  Source,
  ValuationMethod,
  VerdictStatus,
} from "@/lib/api/client";
import { formatFactValue, humanizeMetric } from "@/lib/evidence/confidence";
import { buildCitationIndex, type CitationIndex } from "@/lib/sources/sources";

/*
 * Display rules for deep research reports (`GET /research/{id}`): section and method
 * labels, who wrote each sentence (code from stored records, or the model), the claim
 * check, and the citation -> source-card mapping (every source used is listed in the
 * sidebar, and every citation in the body resolves to one of those cards).
 */

export const RESEARCH_HREF = "/research";

export function researchHref(id: number): string {
  return `/research/${id}`;
}

export function researchForTicker(ticker: string): string {
  return `/research?ticker=${encodeURIComponent(ticker)}`;
}

export function holdingHref(ticker: string): string {
  return `/holdings/${encodeURIComponent(ticker)}`;
}

/** Every section a profile can ask for, in the backend's order, and who writes it. */
export const RESEARCH_SECTIONS: readonly { id: ResearchSectionId; label: string; writtenBy: "code" | "model"; description: string }[] = [
  { id: "business", label: "Business", writtenBy: "model", description: "What the company does and how it earns money." },
  { id: "financial_trend", label: "Financial trend", writtenBy: "code", description: "Verified figures over the last periods, from stored facts." },
  { id: "moat_risks", label: "Moat and risks", writtenBy: "model", description: "What protects the business and what threatens it." },
  { id: "valuation", label: "Valuation", writtenBy: "code", description: "The valuation methods below, computed from stored figures and prices." },
  { id: "strategy_fit", label: "Strategy fit", writtenBy: "model", description: "How it fits the strategy's targets, bands and rules." },
  { id: "ownership", label: "Ownership", writtenBy: "code", description: "Institutional holders from SEC Form 13F (US listings)." },
  { id: "relationships", label: "Relationships", writtenBy: "code", description: "Group structure, competitors, suppliers and customers." },
  { id: "risk", label: "Risk", writtenBy: "code", description: "Volatility, drawdown, beta and correlations from price history." },
  { id: "liquidity", label: "Liquidity", writtenBy: "code", description: "Turnover on the home market against the position size." },
  { id: "open_questions", label: "Open questions", writtenBy: "model", description: "What the sources do not answer yet." },
];
export const SECTION_IDS = RESEARCH_SECTIONS.map((s) => s.id);

export function sectionLabel(id: string): string {
  return RESEARCH_SECTIONS.find((s) => s.id === id)?.label ?? id.replaceAll("_", " ");
}

export const VALUATION_METHODS: readonly { id: ValuationMethod; label: string; description: string }[] = [
  { id: "pe_ttm", label: "P/E (TTM)", description: "Price over trailing-twelve-month earnings per share." },
  { id: "earnings_yield", label: "Earnings yield", description: "Trailing-twelve-month EPS over the price." },
  { id: "dividend_yield_ttm", label: "Dividend yield (TTM)", description: "Dividends of the last twelve months over the price." },
];

export function valuationLabel(method: string): string {
  return VALUATION_METHODS.find((m) => m.id === method)?.label ?? method.replaceAll("_", " ");
}

export const DEPTHS = [
  { value: "brief", label: "Brief: 3 sentences per model section" },
  { value: "standard", label: "Standard: 6 sentences per model section" },
  { value: "deep", label: "Deep: 12 sentences per model section" },
] as const;

export const WRITTEN_BY: Record<"code" | "model", { label: string; description: string }> = {
  code: { label: "Computed", description: "Written by code from stored records; every figure cites the record it comes from." },
  model: { label: "Model-written", description: "Written by the model from the gathered sources; each sentence cites them." },
};

export function writtenBy(value: string | null | undefined): "code" | "model" {
  return value === "model" ? "model" : "code";
}

// ---------------------------------------------------------------------------
// Citations
// ---------------------------------------------------------------------------

/** Every citation list in a research body: each sentence and each valuation row. */
export function researchCitations(body: Pick<ResearchBody, "sections" | "valuation">): (readonly string[])[] {
  return [
    ...body.sections.flatMap((section) => section.sentences.map((s: CitedSentence) => s.source_ids ?? [])),
    ...body.valuation.map((v) => v.source_ids),
  ];
}

export function researchIndex(body: Pick<ResearchBody, "sections" | "valuation" | "sources">): CitationIndex {
  return buildCitationIndex(body.sources, researchCitations(body));
}

// ---------------------------------------------------------------------------
// Profiles offered when starting a run
// ---------------------------------------------------------------------------

export type ProfileOption = { id: string; name: string };

/** The backend's default profiles, offered when nothing is saved (a 404). */
export const DEFAULT_PROFILE_OPTIONS: readonly ProfileOption[] = [
  { id: "standard", name: "Standard workup" },
  { id: "quick", name: "Quick screen" },
];

export function profileOptions(config: Pick<ResearchProfilesConfig, "profiles" | "default_profile"> | null): {
  options: ProfileOption[];
  defaultId: string;
} {
  if (!config || !config.profiles || config.profiles.length === 0) {
    return { options: [...DEFAULT_PROFILE_OPTIONS], defaultId: "standard" };
  }
  const options = config.profiles.map((p) => ({ id: p.id, name: p.name }));
  const defaultId = options.some((o) => o.id === config.default_profile) ? config.default_profile : options[0].id;
  return { options, defaultId };
}

/** A ticker the API accepts (letters, digits, ".", "_", "-"; 1–32). */
export function normaliseTicker(value: string): string | null {
  const t = value.trim();
  return /^[A-Za-z0-9._-]{1,32}$/.test(t) ? t : null;
}

// ---------------------------------------------------------------------------
// Report page: run pipeline, claim check, key figures, KPI figure, run details
// ---------------------------------------------------------------------------

/** "write_business" -> "Write business", "verify_claims" -> "Verify claims". */
export function stepLabel(name: string): string {
  const text = name.replaceAll("_", " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The section shown as the report's conclusion, when the run wrote one ("summary" or "conclusion"). */
export function conclusionSection<S extends { id: string }>(sections: readonly S[]): S | null {
  return sections.find((s) => s.id === "summary" || s.id === "conclusion") ?? null;
}

/** "13 / 15": sentences the claim verifier supported, of all it looked at (removed ones excluded). */
export function claimTally(
  claims: Pick<ClaimCheckSummary, "supported" | "unsupported" | "unchecked"> | null | undefined,
): { supported: number; total: number } | null {
  if (!claims) return null;
  const total = claims.supported + claims.unsupported + claims.unchecked;
  return total > 0 ? { supported: claims.supported, total } : null;
}

/** How a run was requested, in words: "By hand", "On schedule", or the trigger as stored. */
export function triggerLabel(trigger: string | null | undefined): string {
  if (!trigger) return "—";
  const known: Record<string, string> = { manual: "By hand", schedule: "On schedule", scheduled: "On schedule", chat: "From chat" };
  return known[trigger] ?? stepLabel(trigger);
}

type KeyFact = Pick<
  FactOut,
  "id" | "metric" | "basis" | "status" | "value_units" | "period_end" | "fiscal_year" | "fiscal_period" | "period"
> &
  Parameters<typeof formatFactValue>[0];

function periodOrder(f: Pick<FactOut, "period_end" | "fiscal_year" | "fiscal_period">): string {
  return f.period_end ?? `${f.fiscal_year}-${f.fiscal_period}`;
}

/** The same metric's stored figures (same basis, not rejected), oldest first, one per period. */
export function metricSeries<F extends KeyFact>(fact: Pick<F, "metric" | "basis">, facts: readonly F[]): F[] {
  const byPeriod = new Map<string, F>();
  for (const f of facts) {
    if (f.metric !== fact.metric || f.basis !== fact.basis || f.status === "rejected") continue;
    const key = periodOrder(f);
    const seen = byPeriod.get(key);
    if (!seen || (seen.status !== "verified" && f.status === "verified")) byPeriod.set(key, f);
  }
  return [...byPeriod.values()].sort((a, b) => periodOrder(a).localeCompare(periodOrder(b)));
}

export type KeyFigure = { sourceId: string; factId: number; label: string; value: string; series: number[] };

/**
 * One row per fact the report cites (by its source card, `kind: "fact"`, `ref` = fact id),
 * once per metric: the cited value as printed and the metric's stored history for a
 * sparkline. Facts not found in `facts` (e.g. the holding page could not be read) are left out.
 */
export function keyFigures<F extends KeyFact>(sources: readonly Pick<Source, "id" | "kind" | "ref">[], facts: readonly F[]): KeyFigure[] {
  const byId = new Map(facts.map((f) => [f.id, f]));
  const seen = new Set<string>();
  const out: KeyFigure[] = [];
  for (const s of sources) {
    if (s.kind !== "fact") continue;
    const fact = byId.get(Number(s.ref));
    if (!fact) continue;
    const key = `${fact.metric}|${fact.basis}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      sourceId: s.id,
      factId: fact.id,
      label: `${humanizeMetric(fact.metric)}${fact.basis === "adjusted" ? " (adj.)" : ""} · ${fact.period}`,
      value: formatFactValue(fact),
      series: metricSeries(fact, facts).map((f) => f.value_units),
    });
  }
  return out;
}

/** A KPI's value from a fact, in the KPI's unit: "%" and "x" as printed, "EUR bn" / "USD m" rescaled. */
export function factInKpiUnit(fact: Pick<FactOut, "value" | "value_units" | "unit">, kpiUnit: string): number {
  if (fact.unit === "percent" || fact.unit === "ratio" || fact.unit === "per_share") return fact.value;
  const scale = /\bbn$/i.test(kpiUnit) ? 1e9 : /\bm$/i.test(kpiUnit) ? 1e6 : /\bk$/i.test(kpiUnit) ? 1e3 : 1;
  return fact.value_units / scale;
}

export type KpiFigure = {
  kpi: KpiStatus["kpi"];
  /** The KPI's current verdict; unknown when never judged. */
  status: VerdictStatus;
  points: { label: string; value: number }[];
};

/**
 * The thesis KPI to chart against its threshold: one whose metric the report cites and that
 * has at least two stored figures, a breaching one first. Null when there is none (no KPI
 * history, or the report cites none of the KPIs' metrics).
 */
export function kpiFigure<F extends KeyFact & Pick<FactOut, "value" | "unit">>(
  kpis: readonly Pick<KpiStatus, "kpi" | "current">[],
  facts: readonly F[],
  citedFactIds: readonly number[],
): KpiFigure | null {
  const cited = new Set(facts.filter((f) => citedFactIds.includes(f.id)).map((f) => `${f.metric}|${f.basis}`));
  const candidates = kpis
    .filter((k) => cited.has(`${k.kpi.metric}|${k.kpi.basis}`))
    .map((k) => {
      const series = metricSeries({ metric: k.kpi.metric, basis: k.kpi.basis }, facts);
      return {
        kpi: k.kpi,
        status: k.current?.status ?? "unknown",
        points: series.map((f) => ({ label: `${f.fiscal_period}·${String(f.fiscal_year).slice(-2)}`, value: factInKpiUnit(f, k.kpi.unit) })),
      };
    })
    .filter((c) => c.points.length >= 2);
  if (candidates.length === 0) return null;
  return candidates.find((c) => c.status === "breach") ?? candidates[0];
}
