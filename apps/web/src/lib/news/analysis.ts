import type { components } from "@/lib/api/schema";

/* Labels and small helpers for the news analysis surfaces: the wire, stories, briefs. */

type Schemas = components["schemas"];

/** Response fields with a default are always sent, so the generated optional marks are dropped. */
type Sent<T> = T extends (infer U)[]
  ? Sent<U>[]
  : T extends object
    ? { [K in keyof T]-?: Sent<T[K]> }
    : T;

export type StoryAnalysis = Sent<Schemas["StoryAnalysis"]>;
export type WireItem = Sent<Schemas["WireItem"]>;
export type WirePage = Sent<Schemas["WirePage"]>;
export type ThreadOut = Sent<Schemas["ThreadOut"]>;
export type ThreadDetail = Sent<Schemas["ThreadDetail"]>;
export type BriefOut = Sent<Schemas["BriefOut"]>;
export type ThemeDay = Sent<Schemas["ThemeDay"]>;
export type StudyRow = Sent<Schemas["StudyRow"]>;
export type NewsFeedbackKind = Schemas["NewsFeedbackIn"]["kind"];
export type NewsFeedbackTarget = Schemas["NewsFeedbackIn"]["target"];

export const NOVELTY_LABELS: Record<NonNullable<StoryAnalysis["novelty"]>["novelty"], string> = {
  new: "New",
  update: "Update to an earlier story",
  repeat: "Repeat of an earlier story",
  recycled: "Older news again",
};

export const SURPRISE_LABELS: Record<NonNullable<StoryAnalysis["novelty"]>["surprise"], string> = {
  above: "Above expectations",
  in_line: "In line with expectations",
  below: "Below expectations",
  none: "No expectation",
  unknown: "No expectation stated",
};

export const LIFECYCLE_LABELS: Record<string, string> = {
  reported: "Reported",
  corroborated: "Corroborated",
  primary_confirmed: "Confirmed by a primary source",
  disputed: "Disputed",
  corrected: "Corrected",
  resolved: "Resolved",
};

export const RELATION_LABELS = {
  direct: "Held directly",
  second_order: "Through a supply-chain link",
  attribute: "Shares a sector, country or currency",
  hypothesis: "Possible, not in the graph",
} as const;

export const VERDICT_LABELS = {
  supported: "Supported",
  contradicted: "Contradicted",
  unverified: "Not verified",
} as const;

export const THESIS_STATUS_LABELS = {
  supports: "Supports",
  review: "Worth a review",
  breach: "Breach, confirmed by a primary source",
} as const;

export const EVENT_STATUS_LABELS = {
  rumour: "Rumour",
  reported: "Reported",
  confirmed: "Confirmed",
  official: "Official",
} as const;

export const BRIEF_KIND_LABELS = {
  morning: "Morning brief",
  weekly: "Weekly brief",
  holding: "Holding brief",
  region: "By region",
  theme: "Theme digests",
} as const;

export const SENTIMENT_LABELS = { positive: "Positive", negative: "Negative", mixed: "Mixed", neutral: "Neutral" } as const;

/** GDELT's average tone for a theme: "-2.1" reads as coverage leaning negative. */
export function toneLabel(tone: number | null | undefined): string {
  if (tone === null || tone === undefined) return "no tone data";
  return `${tone > 0 ? "+" : ""}${tone.toFixed(1)} tone`;
}

export const FEEDBACK_KINDS: { id: NewsFeedbackKind; label: string }[] = [
  { id: "wrong_entity", label: "Wrong company" },
  { id: "duplicate", label: "Duplicate" },
  { id: "misread", label: "Misread" },
  { id: "missing_source", label: "A source is missing" },
  { id: "confirmed", label: "I confirmed this myself" },
  { id: "useful", label: "Useful" },
  { id: "not_useful", label: "Not useful" },
];

export const FLAG_LABELS = { spike: "Volume spike", spread: "Spreading to new regions" } as const;

/** A fraction of the portfolio as a percentage: 0.0312 reads "3.1%". */
export function exposurePct(weight: number | null | undefined): string {
  return weight === null || weight === undefined ? "n/a" : `${(weight * 100).toFixed(1)}%`;
}

/** A price move already in percent, with its sign: "+2.0%", "-1.5%". */
export function signedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  return `${value > 0 ? "+" : value < 0 ? "-" : ""}${Math.abs(value).toFixed(1)}%`;
}

export function claimTotals(claims: { verdict: "supported" | "contradicted" | "unverified" }[]) {
  const out = { supported: 0, contradicted: 0, unverified: 0 };
  for (const c of claims) out[c.verdict] += 1;
  return out;
}

export type WireFilters = {
  region: string;
  language: string;
  outlet: string;
  tier: string;
  topic: string;
  linked: "" | "yes" | "no";
  paywall: string;
  q: string;
};

export const EMPTY_WIRE_FILTERS: WireFilters = {
  region: "",
  language: "",
  outlet: "",
  tier: "",
  topic: "",
  linked: "",
  paywall: "",
  q: "",
};

export function parseWireFilters(params: URLSearchParams): WireFilters {
  const linked = params.get("linked");
  return {
    region: (params.get("region") ?? "").toUpperCase().slice(0, 2),
    language: (params.get("language") ?? "").toLowerCase().slice(0, 8),
    outlet: params.get("outlet") ?? "",
    tier: ["1", "2", "3"].includes(params.get("tier") ?? "") ? (params.get("tier") ?? "") : "",
    topic: params.get("topic") ?? "",
    linked: linked === "yes" || linked === "no" ? linked : "",
    paywall: ["open", "metered", "hard"].includes(params.get("paywall") ?? "") ? (params.get("paywall") ?? "") : "",
    q: params.get("q") ?? "",
  };
}

export function wireFiltersToSearch(filters: WireFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value.trim()) params.set(key, value.trim());
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function isWireFiltered(filters: WireFilters): boolean {
  return Object.values(filters).some((v) => v.trim() !== "");
}

/** The API query for the wire: empty filters left out, the linked toggle as a boolean. */
export function wireQuery(filters: WireFilters, beforeId: number | null, limit: number) {
  return {
    region: filters.region.trim() || undefined,
    language: filters.language.trim() || undefined,
    outlet: filters.outlet.trim() || undefined,
    tier: filters.tier ? (Number(filters.tier) as 1 | 2 | 3) : undefined,
    topic: filters.topic.trim() || undefined,
    linked: filters.linked === "" ? undefined : filters.linked === "yes",
    paywall: (filters.paywall || undefined) as "open" | "metered" | "hard" | undefined,
    q: filters.q.trim() || undefined,
    before_id: beforeId ?? undefined,
    limit,
  };
}

/** The headline to show: the English translation when there is one, else the original. */
export function wireHeadline(item: Pick<WireItem, "headline" | "title_en">): { text: string; translated: boolean } {
  return item.title_en && item.title_en !== item.headline
    ? { text: item.title_en, translated: true }
    : { text: item.headline, translated: false };
}
