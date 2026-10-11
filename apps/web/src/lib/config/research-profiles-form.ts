import type {
  ResearchProfile,
  ResearchProfilesConfig,
  ResearchSectionId,
  ValuationMethod,
} from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import type { FieldErrors } from "@/lib/api/validation-errors";
import { SECTION_IDS } from "@/lib/research/report";

/*
 * Form state for the research profiles (`ResearchProfilesConfig`, generated from the
 * backend's Pydantic model, apps/backend/src/steward/config/research.py). A quick screen
 * and a thorough workup are two profiles, not two code paths: each profile picks its
 * sections (in order), depth, model, effort, budgets, source tiers, peers, valuation
 * methods, language, mode, web emphasis, date horizon, domain lists, forums, stale-source
 * limits, iteration, authoring mode and report template. The report templates (sections,
 * order, titles, detail-only, owner-defined sections) are edited here too. Fields this
 * form does not know are kept, so a field the backend adds later survives a save.
 */

export type Depth = ResearchProfile["depth"];
export type Effort = ResearchProfile["effort"];
export type Language = ResearchProfile["language"];
export type Mode = ResearchProfile["mode"];
export type WebEmphasis = ResearchProfile["web_emphasis"];
export type Authoring = ResearchProfile["authoring"];
export type DefaultView = components["schemas"]["ReportTemplate"]["default_view"];
export type Tier = 1 | 2 | 3;

export const MODES: readonly { value: Mode; label: string }[] = [
  { value: "quick", label: "Quick screen" },
  { value: "standard", label: "Standard workup" },
  { value: "deep_thesis", label: "Deep thesis" },
  { value: "earnings_update", label: "Earnings update" },
  { value: "portfolio_macro", label: "Portfolio and macro" },
  { value: "custom", label: "Custom" },
];

export const WEB_EMPHASES: readonly { value: WebEmphasis; label: string }[] = [
  { value: "internal", label: "Stored records first (a quarter of the searches)" },
  { value: "balanced", label: "Balanced (full search budget)" },
  { value: "web", label: "Web first (full search budget)" },
];

export const AUTHORING_MODES: readonly { value: Authoring; label: string }[] = [
  { value: "research_only", label: "Research only" },
  { value: "draft_thesis", label: "Also draft a thesis proposal" },
  { value: "propose_update", label: "Also propose a thesis update" },
  { value: "review_thesis", label: "Also review the active thesis" },
];

export const DEFAULT_VIEWS: readonly { value: DefaultView; label: string }[] = [
  { value: "concise", label: "Concise" },
  { value: "detailed", label: "Detailed" },
];

/** Source kinds a stale limit can be set for, with the built-in limit in days (ledger.py). */
export const STALE_KINDS: readonly { kind: string; label: string; builtin: number }[] = [
  { kind: "price", label: "A price", builtin: 7 },
  { kind: "news_item", label: "A news item", builtin: 90 },
  { kind: "search", label: "A search result", builtin: 180 },
  { kind: "observation", label: "An observation", builtin: 90 },
  { kind: "default", label: "Any other kind", builtin: 550 },
];

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
  date_horizon_days: { min: 7, max: 3650 },
  max_rounds: { min: 1, max: 3 },
  min_sentences: { min: 1, max: 6 },
  stale_days: { min: 1, max: 3650 },
  allow_domains: { max: 20 },
  deny_domains: { max: 40 },
  forum_domains: { max: 40 },
  domain: { max: 100 },
  templates: { max: 20 },
  template_sections: { max: 20 },
  custom_sections: { max: 6 },
  section_title: { max: 80 },
  section_sentences: { min: 1, max: 20 },
  custom_task: { min: 10, max: 400 },
} as const;

const ID_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const CUSTOM_ID_PATTERN = /^custom-[a-z0-9]+(-[a-z0-9]+)*$/;
const DOMAIN_PATTERN = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;
/** The backend rejects owner-defined titles and questions holding any of these. */
const MARKUP_MARKS = ["http:", "https:", "www.", "<", ">", "```", "{{"] as const;

/** The planning, source-policy, iteration and authoring defaults every profile carries. */
export const PROFILE_DEFAULTS = {
  mode: "standard",
  web_emphasis: "balanced",
  allow_forums: false,
  iterate: false,
  max_rounds: 1,
  min_sentences: 2,
  authoring: "research_only",
  planning: "code",
  engine: "fixed",
  lenses: ["financials", "filings_events", "news_web", "portfolio_risk", "counter_case"],
  worker_model: "gpt-6-luna",
  worker_effort: "low",
  worker_max_steps: 12,
  worker_max_searches: 8,
  parallel_workers: 2,
} as const;

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_RESEARCH_PROFILES: ResearchProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      ...PROFILE_DEFAULTS,
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
      model: "gpt-6.1-sol",
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
      ...PROFILE_DEFAULTS,
      mode: "quick",
      web_emphasis: "internal",
      id: "quick",
      name: "Quick screen",
      sections: ["business", "financial_trend", "valuation", "strategy_fit"],
      depth: "brief",
      model: "gpt-6.1-sol",
      effort: "medium",
      max_steps: 20,
      max_searches: 10,
      source_tiers: [1, 2],
      include_peers: false,
      valuation_methods: ["pe_ttm"],
      language: "en",
      top_holders: 3,
    },
    {
      ...PROFILE_DEFAULTS,
      mode: "deep_thesis",
      iterate: true,
      max_rounds: 2,
      authoring: "draft_thesis",
      template_id: "thesis-memo",
      id: "deep-thesis",
      name: "Deep thesis workup",
      sections: [
        "executive_summary",
        "business",
        "financial_trend",
        "moat_risks",
        "valuation",
        "scenarios",
        "catalysts",
        "counter_case",
        "strategy_fit",
        "thesis_kpis",
        "open_questions",
      ],
      depth: "deep",
      model: "gpt-6.1-sol",
      effort: "high",
      max_steps: 100,
      max_searches: 60,
      source_tiers: [1, 2],
      include_peers: true,
      valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
      language: "en",
      top_holders: 3,
    },
    {
      ...PROFILE_DEFAULTS,
      mode: "deep_thesis",
      iterate: true,
      max_rounds: 2,
      authoring: "draft_thesis",
      template_id: "thesis-memo",
      engine: "agentic",
      id: "deep-agentic",
      name: "Deep thesis workup (agents)",
      sections: [
        "executive_summary",
        "business",
        "financial_trend",
        "moat_risks",
        "valuation",
        "scenarios",
        "catalysts",
        "counter_case",
        "strategy_fit",
        "thesis_kpis",
        "open_questions",
      ],
      depth: "deep",
      model: "gpt-6.1-sol",
      effort: "high",
      max_steps: 100,
      max_searches: 60,
      source_tiers: [1, 2],
      include_peers: true,
      valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
      language: "en",
      top_holders: 3,
    },
    {
      ...PROFILE_DEFAULTS,
      mode: "earnings_update",
      iterate: true,
      authoring: "propose_update",
      template_id: "update-note",
      date_horizon_days: 120,
      id: "earnings-update",
      name: "Earnings and thesis update",
      sections: [
        "executive_summary",
        "financial_trend",
        "thesis_kpis",
        "counter_case",
        "strategy_fit",
        "open_questions",
      ],
      depth: "standard",
      model: "gpt-6.1-sol",
      effort: "high",
      max_steps: 40,
      max_searches: 20,
      source_tiers: [1, 2],
      include_peers: true,
      valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
      language: "en",
      top_holders: 3,
    },
  ],
  default_profile: "standard",
  templates: [
    {
      schema_version: 1,
      id: "thesis-memo",
      name: "Thesis memo",
      default_view: "concise",
      custom_sections: [],
      sections: [
        { id: "executive_summary", enabled: true, detail_only: false },
        { id: "business", enabled: true, detail_only: false },
        { id: "financial_trend", enabled: true, detail_only: true },
        { id: "valuation", enabled: true, detail_only: false },
        { id: "scenarios", enabled: true, detail_only: false },
        { id: "catalysts", enabled: true, detail_only: true },
        { id: "moat_risks", enabled: true, detail_only: true },
        { id: "counter_case", enabled: true, detail_only: false },
        { id: "strategy_fit", enabled: true, detail_only: false },
        { id: "thesis_kpis", enabled: true, detail_only: false },
        { id: "open_questions", enabled: true, detail_only: false },
      ],
    },
    {
      schema_version: 1,
      id: "update-note",
      name: "Update note",
      default_view: "concise",
      custom_sections: [],
      sections: [
        { id: "executive_summary", enabled: true, detail_only: false },
        { id: "financial_trend", enabled: true, detail_only: false },
        { id: "thesis_kpis", enabled: true, detail_only: false },
        { id: "counter_case", enabled: true, detail_only: false },
        { id: "strategy_fit", enabled: true, detail_only: false },
        { id: "open_questions", enabled: true, detail_only: false },
      ],
    },
  ],
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
  mode: Mode;
  web_emphasis: WebEmphasis;
  /** Days back to prefer evidence from; blank = no horizon. */
  date_horizon_days: string;
  /** Domain lists are typed one per line (commas and spaces also split). */
  allow_domains: string;
  deny_domains: string;
  allow_forums: boolean;
  forum_domains: string;
  /** Days per source kind; blank = the built-in limit. */
  stale_days: Record<string, string>;
  iterate: boolean;
  max_rounds: string;
  min_sentences: string;
  /** Id of a report template; blank = none. */
  template_id: string;
  authoring: Authoring;
  rest: Record<string, unknown>;
};

/** One section row of a report template. */
export type TemplateSectionForm = {
  /** Stable React key; not stored. */
  key: string;
  /** A built-in section id, or the id of one of the template's owner-defined sections. */
  id: string;
  enabled: boolean;
  /** Title override; blank = the section's own title. */
  title: string;
  /** Sentence cap override; blank = the profile's depth decides. */
  max_sentences: string;
  detail_only: boolean;
  rest: Record<string, unknown>;
};

/** A section the owner defines: a title and the question it answers. */
export type CustomSectionForm = {
  key: string;
  id: string;
  title: string;
  task: string;
  rest: Record<string, unknown>;
};

export type TemplateForm = {
  key: string;
  id: string;
  name: string;
  default_view: DefaultView;
  /** Ordered: the report follows this order. */
  sections: TemplateSectionForm[];
  custom_sections: CustomSectionForm[];
  rest: Record<string, unknown>;
};

export type ResearchProfilesForm = {
  profiles: ProfileForm[];
  default_profile: string;
  templates: TemplateForm[];
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
    model: "gpt-6.1-sol",
    effort: "high",
    max_steps: "20",
    max_searches: "15",
    source_tiers: [1, 2],
    include_peers: true,
    valuation_methods: ["pe_ttm", "earnings_yield", "dividend_yield_ttm"],
    language: "en",
    top_holders: "3",
    mode: PROFILE_DEFAULTS.mode,
    web_emphasis: PROFILE_DEFAULTS.web_emphasis,
    date_horizon_days: "",
    allow_domains: "",
    deny_domains: "",
    allow_forums: PROFILE_DEFAULTS.allow_forums,
    forum_domains: "",
    stale_days: {},
    iterate: PROFILE_DEFAULTS.iterate,
    max_rounds: String(PROFILE_DEFAULTS.max_rounds),
    min_sentences: String(PROFILE_DEFAULTS.min_sentences),
    template_id: "",
    authoring: PROFILE_DEFAULTS.authoring,
    rest: {},
  };
}

// ---------------------------------------------------------------------------
// Domain lists: typed as text, stored as a list
// ---------------------------------------------------------------------------

/** "a.com, B.com\nc.org" -> ["a.com", "b.com", "c.org"]: lower-cased, no blanks, no repeats. */
export function parseDomains(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const d = raw.trim().toLowerCase();
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}

export function domainsToText(domains: readonly string[] | undefined | null): string {
  return (domains ?? []).join("\n");
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
    stale_days: { ...profile.stale_days },
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
    mode,
    web_emphasis,
    date_horizon_days,
    allow_domains,
    deny_domains,
    allow_forums,
    forum_domains,
    stale_days,
    iterate,
    max_rounds,
    min_sentences,
    template_id,
    authoring,
    ...rest
  } = p;
  return {
    key: nextKey(),
    id,
    name,
    sections: [...sections],
    depth: depth ?? "standard",
    model: model ?? "gpt-6.1-sol",
    effort: effort ?? "high",
    max_steps: String(max_steps ?? 20),
    max_searches: String(max_searches ?? 15),
    source_tiers: [...(source_tiers ?? [1, 2])] as Tier[],
    include_peers: include_peers ?? true,
    valuation_methods: [...(valuation_methods ?? ["pe_ttm", "earnings_yield", "dividend_yield_ttm"])],
    language: language ?? "en",
    top_holders: String(top_holders ?? 3),
    mode: mode ?? PROFILE_DEFAULTS.mode,
    web_emphasis: web_emphasis ?? PROFILE_DEFAULTS.web_emphasis,
    date_horizon_days: date_horizon_days == null ? "" : String(date_horizon_days),
    allow_domains: domainsToText(allow_domains),
    deny_domains: domainsToText(deny_domains),
    allow_forums: allow_forums ?? PROFILE_DEFAULTS.allow_forums,
    forum_domains: domainsToText(forum_domains),
    stale_days: Object.fromEntries(Object.entries(stale_days ?? {}).map(([k, v]) => [k, String(v)])),
    iterate: iterate ?? PROFILE_DEFAULTS.iterate,
    max_rounds: String(max_rounds ?? PROFILE_DEFAULTS.max_rounds),
    min_sentences: String(min_sentences ?? PROFILE_DEFAULTS.min_sentences),
    template_id: template_id ?? "",
    authoring: authoring ?? PROFILE_DEFAULTS.authoring,
    rest,
  };
}

export function researchProfilesFromBody(body: ResearchProfilesConfig | null): ResearchProfilesForm {
  const b = body ?? DEFAULT_RESEARCH_PROFILES;
  const { profiles, default_profile, templates, schema_version: _version, ...rest } = b;
  void _version;
  return {
    profiles: (profiles ?? DEFAULT_RESEARCH_PROFILES.profiles!).map(profileFromBody),
    default_profile: default_profile ?? "standard",
    // A body without templates gets the backend's defaults, as the backend itself does.
    templates: (templates ?? DEFAULT_RESEARCH_PROFILES.templates!).map(templateFromBody),
    rest,
  };
}

function intOrText(text: string): number | string {
  const t = text.trim();
  return /^-?\d+$/.test(t) ? Number(t) : t;
}

function profileToBody(p: ProfileForm): ResearchProfile {
  // Optional fields are left out when empty, so a profile that never set them stays as it was.
  const staleDays = Object.fromEntries(
    Object.entries(p.stale_days)
      .filter(([, v]) => v.trim() !== "")
      .map(([k, v]) => [k, intOrText(v)]),
  );
  const allow = parseDomains(p.allow_domains);
  const deny = parseDomains(p.deny_domains);
  const forums = parseDomains(p.forum_domains);
  return {
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
    mode: p.mode,
    web_emphasis: p.web_emphasis,
    allow_forums: p.allow_forums,
    iterate: p.iterate,
    max_rounds: intOrText(p.max_rounds) as number,
    min_sentences: intOrText(p.min_sentences) as number,
    authoring: p.authoring,
    ...(p.date_horizon_days.trim() ? { date_horizon_days: intOrText(p.date_horizon_days) as number } : {}),
    ...(allow.length > 0 ? { allow_domains: allow } : {}),
    ...(deny.length > 0 ? { deny_domains: deny } : {}),
    ...(forums.length > 0 ? { forum_domains: forums } : {}),
    ...(Object.keys(staleDays).length > 0 ? { stale_days: staleDays as Record<string, number> } : {}),
    ...(p.template_id.trim() ? { template_id: p.template_id.trim() } : {}),
  } as ResearchProfile;
}

export function researchProfilesToBody(form: ResearchProfilesForm): ResearchProfilesConfig {
  return {
    ...form.rest,
    schema_version: 1,
    profiles: form.profiles.map(profileToBody),
    default_profile: form.default_profile.trim(),
    templates: form.templates.map(templateToBody),
  } as ResearchProfilesConfig;
}

// ---------------------------------------------------------------------------
// Report templates
// ---------------------------------------------------------------------------

type TemplateBody = components["schemas"]["ReportTemplate"];
type TemplateSectionBody = components["schemas"]["TemplateSection"];
type CustomSectionBody = components["schemas"]["CustomSection"];

function sectionFromBody(s: TemplateSectionBody): TemplateSectionForm {
  const { id, enabled, title, max_sentences, detail_only, ...rest } = s;
  return {
    key: nextKey(),
    id,
    enabled: enabled ?? true,
    title: title ?? "",
    max_sentences: max_sentences == null ? "" : String(max_sentences),
    detail_only: detail_only ?? false,
    rest,
  };
}

function customFromBody(c: CustomSectionBody): CustomSectionForm {
  const { id, title, task, ...rest } = c;
  return { key: nextKey(), id, title, task, rest };
}

export function templateFromBody(t: TemplateBody): TemplateForm {
  const { id, name, default_view, sections, custom_sections, schema_version: _version, ...rest } = t;
  void _version;
  return {
    key: nextKey(),
    id,
    name,
    default_view: default_view ?? "detailed",
    sections: sections.map(sectionFromBody),
    custom_sections: (custom_sections ?? []).map(customFromBody),
    rest,
  };
}

export function templateToBody(t: TemplateForm): TemplateBody {
  return {
    ...t.rest,
    schema_version: 1,
    id: t.id.trim(),
    name: t.name.trim(),
    default_view: t.default_view,
    sections: t.sections.map((s) => ({
      ...s.rest,
      id: s.id,
      enabled: s.enabled,
      detail_only: s.detail_only,
      ...(s.title.trim() ? { title: s.title.trim() } : {}),
      ...(s.max_sentences.trim() ? { max_sentences: intOrText(s.max_sentences) as number } : {}),
    })),
    custom_sections: t.custom_sections.map((c) => ({ ...c.rest, id: c.id, title: c.title.trim(), task: c.task.trim() })),
  } as TemplateBody;
}

/** A template that shows every section the profile-less default run would, all enabled. */
export function newTemplate(existingIds: readonly string[] = []): TemplateForm {
  return {
    key: nextKey(),
    id: uniqueId("new-template", existingIds),
    name: "New template",
    default_view: "detailed",
    sections: ["executive_summary", "business", "valuation", "open_questions"].map((id) => newTemplateSection(id)),
    custom_sections: [],
    rest: {},
  };
}

function newTemplateSection(id: string): TemplateSectionForm {
  return { key: nextKey(), id, enabled: true, title: "", max_sentences: "", detail_only: false, rest: {} };
}

export function duplicateTemplate(t: TemplateForm, existingIds: readonly string[]): TemplateForm {
  return {
    ...t,
    key: nextKey(),
    id: uniqueId(t.id || "template", existingIds),
    name: `${t.name} (copy)`.slice(0, BOUNDS.name.max),
    sections: t.sections.map((s) => ({ ...s, key: nextKey(), rest: { ...s.rest } })),
    custom_sections: t.custom_sections.map((c) => ({ ...c, key: nextKey(), rest: { ...c.rest } })),
    rest: { ...t.rest },
  };
}

/** Built-in sections the template does not list yet, in the backend's order. */
export function unusedBuiltinSections(t: TemplateForm): ResearchSectionId[] {
  const used = new Set(t.sections.map((s) => s.id));
  return SECTION_IDS.filter((id) => !used.has(id));
}

export function isCustomSectionId(id: string, t: TemplateForm): boolean {
  return t.custom_sections.some((c) => c.id === id);
}

/** Add a built-in section at the end. */
export function addBuiltinSection(t: TemplateForm, id: string): TemplateForm {
  if (t.sections.some((s) => s.id === id) || t.sections.length >= BOUNDS.template_sections.max) return t;
  return { ...t, sections: [...t.sections, newTemplateSection(id)] };
}

/** Add an owner-defined section at the end (the backend allows up to six); its id is made here. */
export function addCustomSection(t: TemplateForm): TemplateForm {
  if (t.custom_sections.length >= BOUNDS.custom_sections.max || t.sections.length >= BOUNDS.template_sections.max) return t;
  const id = uniqueId("custom-section", t.custom_sections.map((c) => c.id));
  return {
    ...t,
    sections: [...t.sections, newTemplateSection(id)],
    custom_sections: [...t.custom_sections, { key: nextKey(), id, title: "", task: "", rest: {} }],
  };
}

/** Remove a section from the template; an owner-defined one is deleted with its question. */
export function removeTemplateSection(t: TemplateForm, id: string): TemplateForm {
  return {
    ...t,
    sections: t.sections.filter((s) => s.id !== id),
    custom_sections: t.custom_sections.filter((c) => c.id !== id),
  };
}

/** Move a section up (-1) or down (+1); unchanged at either end. */
export function moveTemplateSection(t: TemplateForm, id: string, direction: -1 | 1): TemplateForm {
  const row = t.sections.find((s) => s.id === id);
  return row ? { ...t, sections: moveInList(t.sections, row, direction) } : t;
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
    if (!/^(gpt|gemini)-[a-z0-9.-]{1,64}$/.test(p.model.trim())) add(`profiles.${i}.model`, "A gpt- or gemini- model id, e.g. gpt-6.1-sol.");
    const steps = intIn(p.max_steps, BOUNDS.max_steps.min, BOUNDS.max_steps.max);
    if (steps) add(`profiles.${i}.max_steps`, steps);
    const searches = intIn(p.max_searches, BOUNDS.max_searches.min, BOUNDS.max_searches.max);
    if (searches) add(`profiles.${i}.max_searches`, searches);
    const holders = intIn(p.top_holders, BOUNDS.top_holders.min, BOUNDS.top_holders.max);
    if (holders) add(`profiles.${i}.top_holders`, holders);
    if (p.source_tiers.length === 0) add(`profiles.${i}.source_tiers`, "Allow at least one tier.");
    if (new Set(p.sections).size !== p.sections.length) add(`profiles.${i}.sections`, "A section is listed twice.");
    validateProfilePolicy(p, i, form.templates, add);
  });
  if (!form.profiles.some((p) => p.id.trim() === form.default_profile.trim())) {
    add("default_profile", "Pick one of the profiles as the default.");
  }
  validateTemplates(form.templates, add);
  return errors;
}

type Add = (path: string, message: string) => void;

/** Planning, source-policy, iteration and template fields of one profile. */
function validateProfilePolicy(p: ProfileForm, i: number, templates: readonly TemplateForm[], add: Add) {
  const at = `profiles.${i}`;
  if (p.date_horizon_days.trim()) {
    const e = intIn(p.date_horizon_days, BOUNDS.date_horizon_days.min, BOUNDS.date_horizon_days.max);
    if (e) add(`${at}.date_horizon_days`, e);
  }
  const rounds = intIn(p.max_rounds, BOUNDS.max_rounds.min, BOUNDS.max_rounds.max);
  if (rounds) add(`${at}.max_rounds`, rounds);
  const sentences = intIn(p.min_sentences, BOUNDS.min_sentences.min, BOUNDS.min_sentences.max);
  if (sentences) add(`${at}.min_sentences`, sentences);

  const lists = {
    allow_domains: parseDomains(p.allow_domains),
    deny_domains: parseDomains(p.deny_domains),
    forum_domains: parseDomains(p.forum_domains),
  };
  for (const [field, domains] of Object.entries(lists) as [keyof typeof lists, string[]][]) {
    const max = BOUNDS[field].max;
    if (domains.length > max) add(`${at}.${field}`, `At most ${max} domains (there are ${domains.length}).`);
    for (const d of domains) {
      if (d.length > BOUNDS.domain.max) add(`${at}.${field}`, `${d}: at most ${BOUNDS.domain.max} characters.`);
      else if (!DOMAIN_PATTERN.test(d)) add(`${at}.${field}`, `${d}: not a bare domain name (e.g. example.com, no https:// or path).`);
    }
  }
  const deny = new Set(lists.deny_domains);
  const allow = new Set(lists.allow_domains);
  const both = lists.allow_domains.filter((d) => deny.has(d));
  if (both.length > 0) add(`${at}.allow_domains`, `Also in the denied list: ${both.join(", ")}. A domain cannot be both allowed and denied.`);
  const forumClash = lists.forum_domains.filter((d) => deny.has(d) || allow.has(d));
  if (forumClash.length > 0) add(`${at}.forum_domains`, `Also in the allowed or denied list: ${forumClash.join(", ")}.`);

  for (const [kind, text] of Object.entries(p.stale_days)) {
    if (!STALE_KINDS.some((k) => k.kind === kind)) {
      add(`${at}.stale_days`, `Unknown source kind: ${kind}.`);
      continue;
    }
    if (!text.trim()) continue;
    const e = intIn(text, BOUNDS.stale_days.min, BOUNDS.stale_days.max);
    if (e) add(`${at}.stale_days.${kind}`, e);
  }

  const template = p.template_id.trim();
  if (template && !templates.some((t) => t.id.trim() === template)) {
    add(`${at}.template_id`, `No template with the id ${template}. Pick one, or none.`);
  }
}

function plainTextError(value: string): string | null {
  const lowered = value.toLowerCase();
  return MARKUP_MARKS.some((m) => lowered.includes(m)) ? "Write plain words: no links, markup or template syntax." : null;
}

/** Report templates: sections, owner-defined sections, ids and counts, as the backend checks them. */
function validateTemplates(templates: readonly TemplateForm[], add: Add) {
  if (templates.length > BOUNDS.templates.max) add("templates", `At most ${BOUNDS.templates.max} templates.`);
  const seen = new Map<string, number>();
  templates.forEach((t, i) => {
    const at = `templates.${i}`;
    const id = t.id.trim();
    if (!ID_PATTERN.test(id)) add(`${at}.id`, "Lower-case letters and digits, words joined by single dashes (e.g. thesis-memo).");
    else if (id.length > BOUNDS.id.max) add(`${at}.id`, `At most ${BOUNDS.id.max} characters.`);
    if (seen.has(id)) add(`${at}.id`, `Template ${seen.get(id)! + 1} already uses this id.`);
    else seen.set(id, i);
    if (!t.name.trim()) add(`${at}.name`, "Give the template a name.");
    else if (t.name.trim().length > BOUNDS.name.max) add(`${at}.name`, `At most ${BOUNDS.name.max} characters.`);

    if (t.sections.length === 0) add(`${at}.sections`, "Keep at least one section.");
    if (t.sections.length > BOUNDS.template_sections.max) add(`${at}.sections`, `At most ${BOUNDS.template_sections.max} sections.`);
    if (t.sections.length > 0 && !t.sections.some((s) => s.enabled)) add(`${at}.sections`, "Enable at least one section.");

    if (t.custom_sections.length > BOUNDS.custom_sections.max) {
      add(`${at}.custom_sections`, `At most ${BOUNDS.custom_sections.max} sections of your own.`);
    }
    const customIds = t.custom_sections.map((c) => c.id);
    const builtin = new Set<string>(SECTION_IDS);
    const sectionIds = t.sections.map((s) => s.id);
    if (new Set(sectionIds).size !== sectionIds.length) add(`${at}.sections`, "A section is listed twice.");
    if (new Set(customIds).size !== customIds.length) add(`${at}.custom_sections`, "Two sections of your own share an id.");
    const unknown = sectionIds.filter((s) => !builtin.has(s) && !customIds.includes(s));
    if (unknown.length > 0) add(`${at}.sections`, `Unknown sections: ${unknown.join(", ")}.`);
    const unplaced = customIds.filter((c) => !sectionIds.includes(c));
    if (unplaced.length > 0) add(`${at}.custom_sections`, `Defined but not placed in the sections: ${unplaced.join(", ")}.`);

    t.sections.forEach((s, j) => {
      const sat = `${at}.sections.${j}`;
      const title = s.title.trim();
      if (title && title.length > BOUNDS.section_title.max) add(`${sat}.title`, `At most ${BOUNDS.section_title.max} characters.`);
      if (s.max_sentences.trim()) {
        const e = intIn(s.max_sentences, BOUNDS.section_sentences.min, BOUNDS.section_sentences.max);
        if (e) add(`${sat}.max_sentences`, e);
      }
    });
    t.custom_sections.forEach((c, k) => {
      const cat = `${at}.custom_sections.${k}`;
      if (!CUSTOM_ID_PATTERN.test(c.id) || c.id.length > BOUNDS.id.max) add(`${cat}.id`, "Must start with custom- (lower-case words joined by dashes).");
      const title = c.title.trim();
      if (!title) add(`${cat}.title`, "Give the section a title.");
      else if (title.length > BOUNDS.section_title.max) add(`${cat}.title`, `At most ${BOUNDS.section_title.max} characters.`);
      else {
        const e = plainTextError(title);
        if (e) add(`${cat}.title`, e);
      }
      const task = c.task.trim();
      if (task.length < BOUNDS.custom_task.min) add(`${cat}.task`, `Say what the section should answer, in at least ${BOUNDS.custom_task.min} characters.`);
      else if (task.length > BOUNDS.custom_task.max) add(`${cat}.task`, `At most ${BOUNDS.custom_task.max} characters.`);
      else {
        const e = plainTextError(task);
        if (e) add(`${cat}.task`, e);
      }
    });
  });
}
