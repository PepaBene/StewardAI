import type { Source, SourceKind } from "@/lib/api/client";
import type { CitationPreview } from "@/lib/news/digest";

/*
 * The source registry that research reports, monthly reviews and chat answers cite
 * (`Source`: short ids S1, S2, ... that sentences list in `source_ids`). Each source points
 * at an app page (`app_path`: /evidence/12, /holdings/X, /reports/3, ...) and/or its own
 * external page (`url`). These helpers number the sources, map every citation to its card,
 * and decide where a citation links, so every surface links the same way.
 */

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  fact: "Extracted figure",
  news_item: "News item",
  relationship: "Relationship",
  filing13f: "13F filing",
  price: "Price history",
  listing: "Listing",
  verdict: "KPI verdict",
  position: "Position",
  strategy: "Strategy",
  thesis: "Thesis",
  report: "Report",
  search: "Web search",
  observation: "Stored observation",
};

export function sourceKindLabel(kind: string): string {
  return SOURCE_KIND_LABELS[kind as SourceKind] ?? kind.replaceAll("_", " ");
}

/** The in-page anchor of a source card: "source-S3". */
export function sourceAnchor(id: string): string {
  return `source-${id.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

/** An app-internal path: starts with one "/" (never "//host" or a scheme). */
export function isInternalPath(path: string | null | undefined): path is string {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") && !/[\s\\]/.test(path);
}

/** An external link the app may open: http or https only. */
export function isExternalUrl(url: string | null | undefined): url is string {
  if (typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

export type SourceLinks = {
  /** The app page for it (opened in the app), when there is one. */
  internal: string | null;
  /** The source's own page (a new tab), when there is one. */
  external: string | null;
};

export function sourceLinks(source: Pick<Source, "app_path" | "url">): SourceLinks {
  return {
    internal: isInternalPath(source.app_path) ? source.app_path : null,
    external: isExternalUrl(source.url) ? source.url : null,
  };
}

/**
 * Where a citation of this source goes: the app page when there is one (the same evidence
 * and holding pages as the rest of the app), else the external page, else nothing.
 */
export function primarySourceLink(source: Pick<Source, "app_path" | "url">): { href: string; external: boolean } | null {
  const links = sourceLinks(source);
  if (links.internal) return { href: links.internal, external: false };
  if (links.external) return { href: links.external, external: true };
  return null;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function tierText(tier: number | null | undefined): string {
  return typeof tier === "number" ? `Tier ${tier}` : "No tier: a record in this app";
}

/** The hover / focus preview of a citation: the stored snippet and the tier. */
export function sourcePreview(source: Source): CitationPreview {
  const link = primarySourceLink(source);
  return {
    kind: source.kind === "fact" ? "fact" : "note",
    title: source.title,
    snippet: source.snippet?.trim() || null,
    tier: source.tier ?? null,
    tierLabel: tierText(source.tier),
    source: source.url && isExternalUrl(source.url) ? `${sourceKindLabel(source.kind)} · ${hostOf(source.url)}` : sourceKindLabel(source.kind),
    url: link?.href ?? "",
    // This registry spans many source kinds (verdicts, 13F filings, prices, ...) with no
    // single "does it back the claim" or "how was it produced" question in common; only a
    // fact's own EvidenceLink and digest fact citations carry these today.
    relation: null,
    provenance: null,
  };
}

// ---------------------------------------------------------------------------
// Citation -> card mapping
// ---------------------------------------------------------------------------

export type SourceCardEntry = {
  source: Source;
  /** 1-based, in the order of the report's source list. */
  number: number;
  anchor: string;
  /** How many citations point at it. */
  citedBy: number;
};

export type CitationIndex = {
  /** One card per source, in the stored order: every source is listed. */
  cards: SourceCardEntry[];
  byId: Map<string, SourceCardEntry>;
  /** Cited ids with no source card (should never happen; shown as a warning). */
  unresolved: string[];
  /** Sources nothing cites (still listed). */
  uncited: string[];
};

/**
 * Number the sources and resolve each citation to its card. `citations` is every list of
 * source ids in the body (one per sentence, valuation row, flag, ...).
 */
export function buildCitationIndex(sources: readonly Source[], citations: Iterable<readonly string[] | null | undefined>): CitationIndex {
  const cards: SourceCardEntry[] = [];
  const byId = new Map<string, SourceCardEntry>();
  for (const source of sources) {
    if (byId.has(source.id)) continue; // a repeated id is listed once
    const entry: SourceCardEntry = { source, number: cards.length + 1, anchor: sourceAnchor(source.id), citedBy: 0 };
    cards.push(entry);
    byId.set(source.id, entry);
  }
  const unresolved = new Set<string>();
  for (const ids of citations) {
    for (const id of ids ?? []) {
      const card = byId.get(id);
      if (card) card.citedBy += 1;
      else unresolved.add(id);
    }
  }
  return {
    cards,
    byId,
    unresolved: [...unresolved],
    uncited: cards.filter((c) => c.citedBy === 0).map((c) => c.source.id),
  };
}

export type Chip = {
  id: string;
  /** "3", or the raw id when no card has it. */
  label: string;
  /** The card's anchor ("#source-S3"), or null when unresolved. */
  href: string | null;
  card: SourceCardEntry | null;
};

/** The numbered chips of one sentence, in citation order, each once. */
export function chipsFor(ids: readonly string[] | null | undefined, index: CitationIndex): Chip[] {
  return [...new Set(ids ?? [])].map((id) => {
    const card = index.byId.get(id) ?? null;
    return { id, label: card ? String(card.number) : id, href: card ? `#${card.anchor}` : null, card };
  });
}
