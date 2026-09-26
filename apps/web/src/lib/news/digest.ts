import type {
  DigestBody,
  DigestFactSource,
  DigestSentence,
  DigestSource,
  MacroFigure,
  MarketHoliday,
  PortfolioFlag,
  ThesisChange,
} from "@/lib/api/client";

/*
 * Claim-level citations for a digest: each sentence links to the news items and facts it
 * was written from. Sources are numbered in the order they appear in the source list, so
 * the number in the text matches the card in the sidebar.
 */

export type CitationRef = { kind: "news"; id: number } | { kind: "fact"; id: number };

export type CitationLink = {
  key: string;
  label: string;
  href: string;
  title: string;
  external: boolean;
  anchor?: string;
  ref: CitationRef;
};

/** What hovering or focusing a citation shows: the exact text it links to, and its source. */
export type CitationPreview = {
  kind: "news" | "fact" | "note";
  /** The item's headline, or "Revenue Q3 2026: 94,036" for a figure. */
  title: string;
  /** The stored text the citation stands for (news item summary, fact snippet); null if none. */
  snippet: string | null;
  tier: number | null;
  /** "Tier 1", or for a figure its status ("Figure status: verified"). */
  tierLabel: string;
  /** Outlet, or the host of the source URL. */
  source: string;
  url: string;
};

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function tierText(tier: number | null | undefined): string {
  return typeof tier === "number" ? `Tier ${tier}` : "Tier unknown";
}

/**
 * The preview of one digest citation: a news item's headline and stored summary (the text
 * the claim verifier checked the sentence against) with its tier, or a figure's snippet.
 * Null when the digest does not carry the cited item.
 */
export function digestCitationPreview(
  body: Pick<DigestBody, "sources" | "fact_sources">,
  ref: CitationRef,
): CitationPreview | null {
  if (ref.kind === "news") {
    const source = (body.sources ?? []).find((s) => s.news_item_id === ref.id);
    if (!source) return null;
    return {
      kind: "news",
      title: source.headline,
      snippet: source.summary?.trim() || null,
      tier: source.tier,
      tierLabel: tierText(source.tier),
      source: source.outlet ?? hostOf(source.url),
      url: source.url,
    };
  }
  const fact = (body.fact_sources ?? []).find((f) => f.fact_id === ref.id);
  if (!fact) return null;
  return factPreview(fact);
}

export function factPreview(fact: DigestFactSource): CitationPreview {
  return {
    kind: "fact",
    title: `${fact.metric.replaceAll("_", " ")} ${fact.period}: ${fact.value_as_written}`,
    snippet: fact.snippet.trim() || null,
    tier: null,
    tierLabel: `Figure status: ${fact.status}`,
    source: hostOf(fact.source_url),
    url: fact.source_url,
  };
}

export function sourceNumbers(sources: DigestSource[]): Map<number, number> {
  return new Map(sources.map((s, i) => [s.news_item_id, i + 1]));
}

export function sourceAnchor(number: number): string {
  return `source-${number}`;
}

export function citationLinks(
  sentence: Pick<DigestSentence, "news_item_ids" | "fact_ids">,
  sources: DigestSource[],
  numbers: Map<number, number>,
): CitationLink[] {
  const byId = new Map(sources.map((s) => [s.news_item_id, s]));
  const links: CitationLink[] = [];
  for (const id of sentence.news_item_ids ?? []) {
    const source = byId.get(id);
    const number = numbers.get(id);
    if (!source || number === undefined) continue;
    links.push({
      key: `n${id}`,
      label: String(number),
      href: source.url,
      title: `${source.headline} (${source.outlet ?? "source"}, tier ${source.tier})`,
      external: true,
      anchor: sourceAnchor(number),
      ref: { kind: "news", id },
    });
  }
  for (const id of sentence.fact_ids ?? []) {
    links.push({
      key: `f${id}`,
      label: `fact ${id}`,
      href: `/evidence/${id}`,
      title: `Extracted figure #${id}: open its source snippet and archived page`,
      external: false,
      ref: { kind: "fact", id },
    });
  }
  return links;
}

/** Every sentence of the digest (overview and per-holding summaries). */
export function allSentences(body: DigestBody): DigestSentence[] {
  return [...(body.overview ?? []), ...(body.sections ?? []).flatMap((s) => s.summary ?? [])];
}

// ---------------------------------------------------------------------------
// Portfolio, thesis, review, macro and holiday sections
// ---------------------------------------------------------------------------

export const PORTFOLIO_FLAG_LABELS: Record<PortfolioFlag["kind"], string> = {
  band: "Outside its band",
  hard_limit: "Hard limit breached",
  rule: "Strategy rule breached",
};

/** Flags still open first, then by severity (highest first), then by key. */
export function sortFlags(flags: PortfolioFlag[]): PortfolioFlag[] {
  return [...flags].sort((a, b) => {
    const open = Number(a.cleared_at !== null) - Number(b.cleared_at !== null);
    if (open !== 0) return open;
    if (a.severity !== b.severity) return b.severity - a.severity;
    return a.key.localeCompare(b.key);
  });
}

export const VERDICT_WORDS: Record<ThesisChange["to_status"], string> = {
  pass: "pass",
  breach: "breach",
  unknown: "unknown",
};

/** "never judged → breach", "pass → breach". */
export function thesisChangeText(change: Pick<ThesisChange, "from_status" | "to_status">): string {
  const from = change.from_status ? VERDICT_WORDS[change.from_status] : "never judged";
  return `${from} → ${VERDICT_WORDS[change.to_status]}`;
}

/** A macro figure in its own unit: "2.4 %", "101.3 index", "4.33 %". */
export function formatMacroValue(value: number, unit: string | null, locale?: string): string {
  if (!Number.isFinite(value)) return "—";
  const text = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value);
  return unit ? `${text} ${unit}` : text;
}

/** Newly released figures first, then by label. */
export function sortMacro(figures: MacroFigure[]): MacroFigure[] {
  return [...figures].sort((a, b) => Number(b.new) - Number(a.new) || a.label.localeCompare(b.label));
}

/** Holidays grouped by date (ascending), each listing the calendars closed that day. */
export function groupHolidays(holidays: MarketHoliday[]): { date: string; entries: MarketHoliday[] }[] {
  const byDate = new Map<string, MarketHoliday[]>();
  for (const h of [...holidays].sort((a, b) => a.date.localeCompare(b.date) || a.calendar.localeCompare(b.calendar))) {
    byDate.set(h.date, [...(byDate.get(h.date) ?? []), h]);
  }
  return [...byDate].map(([date, entries]) => ({ date, entries }));
}

/** Whether any of the digest's portfolio, thesis, review, macro or holiday lists has entries. */
export function hasStatusSections(body: DigestBody): boolean {
  return [body.portfolio, body.thesis_changes, body.reviews_due, body.macro, body.holidays].some((l) => (l ?? []).length > 0);
}
