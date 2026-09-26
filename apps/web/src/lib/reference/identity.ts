import type {
  HoldingIdentity,
  ListingOut,
  RelationshipDecision,
  RelationshipOut,
  RelationshipType,
  ReportingExceptionOut,
} from "@/lib/api/client";

/*
 * Presentation rules for a holding's identity and group structure (Phase 2b): labels,
 * ordering and the texts shown after a decision. Pure, so every rule is unit-tested.
 */

/** GLEIF Level 2 describes accounting consolidation, not shareholdings. */
export const CONSOLIDATION_NOTE =
  "GLEIF Level 2 reports accounting-consolidation parents, not owners. Shareholders come from filings and voting-rights notices.";

export const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  parent: "Consolidating parent (direct)",
  ultimate_parent: "Ultimate consolidating parent",
  subsidiary: "Subsidiary",
  owner: "Owner",
  competitor: "Competitor",
  supplier: "Supplier",
  customer: "Customer",
};

/** The relationship types a confirmation writes into the holding profile (news matching). */
export const PROFILE_TYPES: readonly RelationshipType[] = ["competitor", "supplier", "customer"];

const SOURCE_LABELS: Record<string, string> = {
  gleif: "GLEIF",
  wikidata: "Wikidata",
  edgar: "SEC EDGAR",
  owner: "You",
  openfigi: "OpenFIGI",
  firds: "ESMA FIRDS",
  esma: "ESMA",
  profile: "Holding profile",
  trading212: "Trading212",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source.toLowerCase()] ?? source;
}

/** "Tier 1 (regulator or filing)", "Tier 3 (community-edited)". */
export function tierLabel(tier: number): string {
  if (tier === 1) return "Tier 1 · regulator or filing";
  if (tier === 2) return "Tier 2";
  if (tier === 3) return "Tier 3 · community-edited";
  return `Tier ${tier}`;
}

export function gleifHref(lei: string): string {
  return `https://search.gleif.org/#/record/${encodeURIComponent(lei)}`;
}

export function wikidataHref(qid: string): string {
  return `https://www.wikidata.org/wiki/${encodeURIComponent(qid)}`;
}

/** Only http(s) links are rendered as links. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  return /^https?:\/\//i.test(url) ? url : null;
}

/** "NATURAL_PERSONS" -> "Natural persons"; free text is kept as is. */
export function humanizeCode(code: string): string {
  if (!/^[A-Z0-9_]+$/.test(code)) return code;
  const words = code.toLowerCase().replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const EXCEPTION_CATEGORIES: Record<string, string> = {
  DIRECT_ACCOUNTING_CONSOLIDATION_PARENT: "No direct consolidating parent reported",
  ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT: "No ultimate consolidating parent reported",
};

/** GLEIF's reporting exception as a sentence: "No direct consolidating parent reported: natural persons." */
export function describeException(exception: ReportingExceptionOut): string {
  const what = EXCEPTION_CATEGORIES[exception.category] ?? `${humanizeCode(exception.category)}`;
  const reason = humanizeCode(exception.reason);
  return `${what}: ${reason.charAt(0).toLowerCase()}${reason.slice(1)}.`;
}

export type ListingState = "home" | "active" | "terminated";

export function listingState(listing: ListingOut, now: Date = new Date()): ListingState {
  if (listing.terminated_at && new Date(listing.terminated_at).getTime() <= now.getTime()) return "terminated";
  return listing.home ? "home" : "active";
}

/** Home market first, then active venues by MIC, then terminated venues (latest first). */
export function sortListings(listings: ListingOut[], now: Date = new Date()): ListingOut[] {
  const rank: Record<ListingState, number> = { home: 0, active: 1, terminated: 2 };
  return [...listings].sort((a, b) => {
    const ra = rank[listingState(a, now)];
    const rb = rank[listingState(b, now)];
    if (ra !== rb) return ra - rb;
    if (ra === 2) return (b.terminated_at ?? "").localeCompare(a.terminated_at ?? "");
    return a.mic.localeCompare(b.mic);
  });
}

/** Direct parent before ultimate parent. */
export function sortParents(parents: RelationshipOut[]): RelationshipOut[] {
  const order = (r: RelationshipOut) => (r.type === "parent" ? 0 : r.type === "ultimate_parent" ? 1 : 2);
  return [...parents].sort((a, b) => order(a) - order(b) || a.object_name.localeCompare(b.object_name));
}

/** Confirmed competitors, suppliers and customers grouped by type, in that order; empty groups dropped. */
export function groupRelated(related: RelationshipOut[]): { type: RelationshipType; items: RelationshipOut[] }[] {
  return PROFILE_TYPES.map((type) => ({
    type,
    items: related.filter((r) => r.type === type).sort((a, b) => a.object_name.localeCompare(b.object_name)),
  })).filter((g) => g.items.length > 0);
}

export type WikidataSummary = {
  qid: string;
  industry: string[];
  products: string[];
  ceo: string[];
  chair: string[];
  website: string | null;
  partOf: string[];
  fetchedAt: string | null;
};

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && v.trim() !== "") : [];
}

/** The Wikidata summary (`WikidataSummary`), read defensively: a row stored by an older backend may lack fields. */
export function parseWikidata(raw: HoldingIdentity["wikidata"]): WikidataSummary | null {
  if (!raw || typeof raw !== "object" || typeof raw.qid !== "string" || !raw.qid) return null;
  return {
    qid: raw.qid,
    industry: strings(raw.industry),
    products: strings(raw.products),
    ceo: strings(raw.ceo),
    chair: strings(raw.chair),
    website: typeof raw.website === "string" && raw.website ? raw.website : null,
    partOf: strings(raw.part_of),
    fetchedAt: typeof raw.fetched_at === "string" ? raw.fetched_at : null,
  };
}

/** What the owner is told after confirming or rejecting a suggestion. */
export function decisionMessage(action: "confirm" | "reject", decision: RelationshipDecision): string {
  const { relationship: r, holding_profiles_version_id: version } = decision;
  const what = RELATIONSHIP_LABELS[r.type].toLowerCase();
  if (action === "reject") return `Rejected ${r.object_name} as ${what}. It will not be suggested again.`;
  if (version !== null) {
    return `Confirmed ${r.object_name} as ${what}. Saved as holding-profiles version #${version}; the next news run matches on it.`;
  }
  return `Confirmed ${r.object_name} as ${what}.`;
}

// ---------------------------------------------------------------------------
// Disputed relationships: a tier-3 claim that a tier-1 source contradicts
// ---------------------------------------------------------------------------

export type RelationshipConflict = {
  source: string;
  tier: number | null;
  type: RelationshipType | null;
  objectName: string;
  sourceRef: string | null;
};

const RELATIONSHIP_TYPES = Object.keys(RELATIONSHIP_LABELS) as RelationshipType[];

/** The contradicting claim stored with a disputed relationship ({source, tier, type, object_name, source_ref}). */
export function parseConflict(raw: RelationshipOut["conflict"]): RelationshipConflict | null {
  if (!raw || typeof raw !== "object") return null;
  const name = raw.object_name;
  if (typeof name !== "string" || !name.trim()) return null;
  const type = typeof raw.type === "string" && (RELATIONSHIP_TYPES as string[]).includes(raw.type) ? (raw.type as RelationshipType) : null;
  return {
    source: typeof raw.source === "string" && raw.source ? raw.source : "unknown",
    tier: typeof raw.tier === "number" && Number.isFinite(raw.tier) ? raw.tier : null,
    type,
    objectName: name.trim(),
    sourceRef: typeof raw.source_ref === "string" && raw.source_ref ? raw.source_ref : null,
  };
}

/** What confirming or rejecting a disputed claim means, for the buttons' descriptions. */
export function disputeChoices(r: Pick<RelationshipOut, "object_name" | "type">, conflict: RelationshipConflict | null) {
  const what = RELATIONSHIP_LABELS[r.type].toLowerCase();
  const other = conflict ? `${conflict.objectName} (${sourceLabel(conflict.source)})` : "the other source's version";
  return {
    confirm: `Confirm ${r.object_name} as ${what}: your decision wins over ${other}.`,
    reject: `Reject ${r.object_name} as ${what}: ${other} stands, and this claim is not suggested again.`,
  };
}
