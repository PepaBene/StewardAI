import type {
  ResearchProfile,
  ResearchProfilesConfig,
  ResearchSectionId,
  ValuationMethod,
} from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/validation-errors";

/*
 * Form state for the research profiles (`ResearchProfilesConfig`, generated from the
 * backend's Pydantic model, apps/backend/src/steward/config/research.py). A quick screen
 * and a thorough workup are two profiles, not two code paths: each profile picks its
 * sections (in order), depth, model, effort, budgets, source tiers, peers, valuation
 * methods and language. Fields this form does not know are kept, so a field the backend
 * adds later survives a save.
 */

export type Depth = ResearchProfile["depth"];
export type Effort = ResearchProfile["effort"];
export type Language = ResearchProfile["language"];
export type Tier = 1 | 2 | 3;

export const LANGUAGES: readonly { value: Language; label: string }[] = [
  { value: "en", label: "English" },
  { value: "cs", label: "Czech" },
];

export const TIER_OPTIONS: readonly { value: Tier; label: string }[] = [
  { value: 1, label: "Tier 1: primary (issuer, regulator, exchange)" },
  { value: 2, label: "Tier 2: established outlets" },
  { value: 3, label: "Tier 3: other sites" },
];

export const BOUNDS = {
  max_steps: { min: 1, max: 200 },
  max_searches: { min: 0, max: 100 },
  top_holders: { min: 1, max: 20 },
  name: { max: 80 },
  id: { max: 40 },
} as const;

const ID_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_RESEARCH_PROFILES: ResearchProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      id: "standard",
      name: "Standard workup",
      sections: [
        "business",
        "financial_trend",
        "moat_risks",
        "valuation",
        "ownership",
        "relationships",
        "risk",
        "liquidity",
        "strategy_fit",
        "open_questions",
      ],
      depth: "standard",
      model: "claude-opus-5-5",
      effort: "high",
      max_steps: 60,
      max_searches: 40,
      source_tiers: [1, 2],
      include_peers: true,
      valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
      language: "en",
      top_holders: 3,
    },
    {
      id: "quick",
      name: "Quick screen",
      sections: ["business", "financial_trend", "valuation", "strategy_fit"],
      depth: "brief",
      model: "claude-sonnet-5",
      effort: "medium",
      max_steps: 20,
      max_searches: 10,
      source_tiers: [1, 2],
      include_peers: false,
      valuation_methods: ["pe_ttm"],
      language: "en",
      top_holders: 3,
    },
  ],
  default_profile: "standard",
};

export type ProfileForm = {
  /** Stable React key; not stored. */
  key: string;
  id: string;
  name: string;
  /** Ordered: the report follows this order. */
  sections: ResearchSectionId[];
  depth: Depth;
  model: string;
  effort: Effort;
  max_steps: string;
  max_searches: string;
  source_tiers: Tier[];
  include_peers: boolean;
  valuation_methods: ValuationMethod[];
  language: Language;
  /** Largest 13F holders named in the ownership section. */
  top_holders: string;
  rest: Record<string, unknown>;
};

export type ResearchProfilesForm = {
  profiles: ProfileForm[];
  default_profile: string;
  rest: Record<string, unknown>;
};

let counter = 0;
const nextKey = () => `p${++counter}`;

export function newProfile(existingIds: readonly string[] = []): ProfileForm {
  return {
    key: nextKey(),
    id: uniqueId("new-profile", existingIds),
    name: "New profile",
    sections: ["business", "financial_trend", "valuation", "strategy_fit"],
    depth: "standard",
    model: "claude-opus-5-5",
    effort: "high",
    max_steps: "20",
    max_searches: "15",
    source_tiers: [1, 2],
    include_peers: true,
    valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
    language: "en",
    top_holders: "3",
    rest: {},
  };
}

/** "quick" -> "quick-2" (or -3, ...) when taken. */
export function uniqueId(base: string, existingIds: readonly string[]): string {
  if (!existingIds.includes(base)) return base;
  const stem = base.replace(/-\d+$/, "");
  for (let n = 2; ; n++) {
    const candidate = `${stem}-${n}`;
    if (!existingIds.includes(candidate)) return candidate;
  }
}

export function duplicateProfile(profile: ProfileForm, existingIds: readonly string[]): ProfileForm {
  return {
    ...profile,
    key: nextKey(),
    id: uniqueId(profile.id || "profile", existingIds),
    name: `${profile.name} (copy)`.slice(0, BOUNDS.name.max),
    sections: [...profile.sections],
    source_tiers: [...profile.source_tiers],
    valuation_methods: [...profile.valuation_methods],
    rest: { ...profile.rest },
  };
}

function profileFromBody(p: ResearchProfile): ProfileForm {
  const {
    id,
    name,
    sections,
    depth,
    model,
    effort,
    max_steps,
    max_searches,
    source_tiers,
    include_peers,
    valuation_methods,
    language,
    top_holders,
    ...rest
  } = p;
  return {
    key: nextKey(),
    id,
    name,
    sections: [...sections],
    depth: depth ?? "standard",
    model: model ?? "claude-opus-5-5",
    effort: effort ?? "high",
    max_steps: String(max_steps ?? 20),
    max_searches: String(max_searches ?? 15),
    source_tiers: [...(source_tiers ?? [1, 2])] as Tier[],
    include_peers: include_peers ?? true,
    valuation_methods: [...(valuation_methods ?? ["pe_ttm", "earnings_yield", "dividend_yield_ttm"])],
    language: language ?? "en",
    top_holders: String(top_holders ?? 3),
    rest,
  };
}

export function researchProfilesFromBody(body: ResearchProfilesConfig | null): ResearchProfilesForm {
  const b = body ?? DEFAULT_RESEARCH_PROFILES;
  const { profiles, default_profile, schema_version: _version, ...rest } = b;
  void _version;
  return {
    profiles: (profiles ?? DEFAULT_RESEARCH_PROFILES.profiles!).map(profileFromBody),
    default_profile: default_profile ?? "standard",
    rest,
  };
}

function intOrText(text: string): number | string {
  const t = text.trim();
  return /^-?\d+$/.test(t) ? Number(t) : t;
}

export function researchProfilesToBody(form: ResearchProfilesForm): ResearchProfilesConfig {
  return {
    ...form.rest,
    schema_version: 1,
    profiles: form.profiles.map((p) => ({
      ...p.rest,
      id: p.id.trim(),
      name: p.name.trim(),
      sections: [...p.sections],
      depth: p.depth,
      model: p.model.trim(),
      effort: p.effort,
      // A non-number is sent as typed, so the API's 422 names the field.
      max_steps: intOrText(p.max_steps) as number,
      max_searches: intOrText(p.max_searches) as number,
      // Tiers are stored in ascending order.
      source_tiers: [...p.source_tiers].sort((a, b) => a - b),
      include_peers: p.include_peers,
      valuation_methods: [...p.valuation_methods],
      language: p.language,
      top_holders: intOrText(p.top_holders) as number,
    })),
    default_profile: form.default_profile.trim(),
  } as ResearchProfilesConfig;
}

// ---------------------------------------------------------------------------
// Ordered multi-select of sections
// ---------------------------------------------------------------------------

export function toggleInList<T>(list: readonly T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** Move one selected section up (-1) or down (+1); unchanged at either end. */
export function moveInList<T>(list: readonly T[], value: T, direction: -1 | 1): T[] {
  const i = list.indexOf(value);
  const j = i + direction;
  if (i < 0 || j < 0 || j >= list.length) return [...list];
  const out = [...list];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

// ---------------------------------------------------------------------------
// Client-side checks (the backend's bounds), shown before anything is sent
// ---------------------------------------------------------------------------

function intIn(text: string, min: number, max: number): string | null {
  const t = text.trim();
  if (!/^-?\d+$/.test(t)) return "Type a whole number.";
  const n = Number(t);
  return n < min || n > max ? `From ${min} to ${max}.` : null;
}

/** Errors keyed like the API's (profiles.0.max_steps), so the same fields show them. */
export function validateResearchProfiles(form: ResearchProfilesForm): FieldErrors {
  const errors: FieldErrors = {};
  const add = (path: string, message: string) => (errors[path] ??= []).push(message);
  if (form.profiles.length === 0) add("profiles", "Keep at least one profile.");
  const seen = new Map<string, number>();
  form.profiles.forEach((p, i) => {
    const id = p.id.trim();
    if (!ID_PATTERN.test(id)) add(`profiles.${i}.id`, "Lower-case letters and digits, words joined by single dashes (e.g. quick-screen).");
    else if (id.length > BOUNDS.id.max) add(`profiles.${i}.id`, `At most ${BOUNDS.id.max} characters.`);
    if (seen.has(id)) add(`profiles.${i}.id`, `Profile ${seen.get(id)! + 1} already uses this id.`);
    else seen.set(id, i);
    if (!p.name.trim()) add(`profiles.${i}.name`, "Give the profile a name.");
    else if (p.name.trim().length > BOUNDS.name.max) add(`profiles.${i}.name`, `At most ${BOUNDS.name.max} characters.`);
    if (p.sections.length === 0) add(`profiles.${i}.sections`, "Pick at least one section.");
    if (!/^claude-/.test(p.model.trim())) add(`profiles.${i}.model`, "A Claude model id, e.g. claude-opus-5-5.");
    const steps = intIn(p.max_steps, BOUNDS.max_steps.min, BOUNDS.max_steps.max);
    if (steps) add(`profiles.${i}.max_steps`, steps);
    const searches = intIn(p.max_searches, BOUNDS.max_searches.min, BOUNDS.max_searches.max);
    if (searches) add(`profiles.${i}.max_searches`, searches);
    const holders = intIn(p.top_holders, BOUNDS.top_holders.min, BOUNDS.top_holders.max);
    if (holders) add(`profiles.${i}.top_holders`, holders);
    if (p.source_tiers.length === 0) add(`profiles.${i}.source_tiers`, "Allow at least one tier.");
  });
  if (!form.profiles.some((p) => p.id.trim() === form.default_profile.trim())) {
    add("default_profile", "Pick one of the profiles as the default.");
  }
  return errors;
}
