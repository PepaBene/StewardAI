import type {
  CitedSentence,
  ResearchBody,
  ResearchProfilesConfig,
  ResearchSectionId,
  ValuationMethod,
} from "@/lib/api/client";
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
