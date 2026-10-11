import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import type { ResearchProfilesConfig } from "@/lib/api/client";
import { SECTION_IDS } from "@/lib/research/report";

import {
  addBuiltinSection,
  addCustomSection,
  BOUNDS,
  DEFAULT_RESEARCH_PROFILES,
  domainsToText,
  duplicateProfile,
  duplicateTemplate,
  moveInList,
  moveTemplateSection,
  newProfile,
  newTemplate,
  parseDomains,
  PROFILE_DEFAULTS,
  removeTemplateSection,
  researchProfilesFromBody,
  researchProfilesToBody,
  STALE_KINDS,
  toggleInList,
  uniqueId,
  unusedBuiltinSections,
  validateResearchProfiles,
} from "./research-profiles-form";

const FULL: ResearchProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      ...PROFILE_DEFAULTS,
      id: "deep-dive",
      name: "Deep dive",
      sections: [...SECTION_IDS].reverse(),
      depth: "deep",
      model: "gpt-6.1-sol",
      effort: "max",
      max_steps: 60,
      max_searches: 40,
      source_tiers: [1, 2, 3],
      include_peers: true,
      valuation_methods: ["dividend_yield_ttm", "pe_ttm"],
      language: "cs",
      top_holders: 10,
    },
    {
      ...PROFILE_DEFAULTS,
      id: "screen",
      name: "Screen",
      sections: ["valuation"],
      depth: "brief",
      model: "gpt-6-luna",
      effort: "low",
      max_steps: 1,
      max_searches: 0,
      source_tiers: [1],
      include_peers: false,
      valuation_methods: [],
      language: "en",
      top_holders: 1,
    },
  ],
  default_profile: "screen",
  templates: [],
};

describe("research profiles form", () => {
  it("round-trips every field of a fully populated body", () => {
    expect(researchProfilesToBody(researchProfilesFromBody(FULL))).toEqual(FULL);
  });

  it("starts from the backend defaults when nothing is saved", () => {
    const form = researchProfilesFromBody(null);
    expect(form.profiles.map((p) => p.id)).toEqual([
      "standard",
      "quick",
      "deep-thesis",
      "earnings-update",
    ]);
    expect(researchProfilesToBody(form)).toEqual(DEFAULT_RESEARCH_PROFILES);
  });

  it("keeps fields it does not know yet", () => {
    const future = {
      ...FULL,
      budget_usd: 3,
      profiles: [{ ...FULL.profiles![0], peer_limit: 4 }, FULL.profiles![1]],
    } as unknown as ResearchProfilesConfig;
    expect(researchProfilesToBody(researchProfilesFromBody(future))).toEqual(future);
  });

  it("sends numbers as numbers, and anything else as typed for the API to name", () => {
    const form = researchProfilesFromBody(FULL);
    form.profiles[0].max_steps = " 12 ";
    form.profiles[0].max_searches = "lots";
    form.profiles[0].source_tiers = [3, 1];
    const body = researchProfilesToBody(form);
    expect(body.profiles![0].max_steps).toBe(12);
    expect(body.profiles![0].max_searches).toBe("lots");
    expect(body.profiles![0].source_tiers).toEqual([1, 3]);
  });

  it("orders sections: toggling appends, moving swaps neighbours", () => {
    expect(toggleInList(["business", "valuation"], "risk")).toEqual(["business", "valuation", "risk"]);
    expect(toggleInList(["business", "valuation"], "business")).toEqual(["valuation"]);
    expect(moveInList(["a", "b", "c"], "c", -1)).toEqual(["a", "c", "b"]);
    expect(moveInList(["a", "b", "c"], "a", -1)).toEqual(["a", "b", "c"]);
    expect(moveInList(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
  });

  it("adds and duplicates profiles with unique ids", () => {
    expect(uniqueId("quick", ["standard", "quick", "quick-2"])).toBe("quick-3");
    const form = researchProfilesFromBody(null);
    const copy = duplicateProfile(form.profiles[1], form.profiles.map((p) => p.id));
    expect(copy.id).toBe("quick-2");
    expect(copy.name).toBe("Quick screen (copy)");
    expect(copy.key).not.toBe(form.profiles[1].key);
    copy.sections.push("risk");
    expect(form.profiles[1].sections).not.toContain("risk");
    expect(newProfile(["new-profile"]).id).toBe("new-profile-2");
  });

  it("checks the backend's bounds before sending", () => {
    const form = researchProfilesFromBody(FULL);
    expect(validateResearchProfiles(form)).toEqual({});
    form.profiles[1].id = "deep-dive";
    form.profiles[0].sections = [];
    form.profiles[0].max_steps = "201";
    form.profiles[1].model = "llama-4";
    form.profiles[1].source_tiers = [];
    form.profiles[1].name = " ";
    form.default_profile = "screen";
    const errors = validateResearchProfiles(form);
    expect(Object.keys(errors).sort()).toEqual(
      [
        "default_profile",
        "profiles.0.max_steps",
        "profiles.0.sections",
        "profiles.1.id",
        "profiles.1.model",
        "profiles.1.name",
        "profiles.1.source_tiers",
      ].sort(),
    );
    expect(validateResearchProfiles({ ...form, profiles: [] }).profiles).toEqual(["Keep at least one profile."]);
    form.profiles[0].id = "Bad Id";
    expect(validateResearchProfiles(form)["profiles.0.id"]?.[0]).toMatch(/Lower-case/);
  });
});

describe("research profiles form: top holders", () => {
  it("defaults an old profile to 3 and checks the bounds", () => {
    const { top_holders: _dropped, ...old } = FULL.profiles![0] as Record<string, unknown>;
    void _dropped;
    const form = researchProfilesFromBody({ ...FULL, profiles: [old as never] });
    expect(form.profiles[0].top_holders).toBe("3");
    form.profiles[0].top_holders = "21";
    expect(validateResearchProfiles(form)["profiles.0.top_holders"]).toEqual(["From 1 to 20."]);
    form.profiles[0].top_holders = "5";
    expect(researchProfilesToBody(form).profiles![0].top_holders).toBe(5);
  });
});

const FULL_NEW: ResearchProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      ...PROFILE_DEFAULTS,
      mode: "earnings_update",
      web_emphasis: "web",
      date_horizon_days: 90,
      allow_domains: ["example.com", "sub.example.org"],
      deny_domains: ["spam.net"],
      allow_forums: true,
      forum_domains: ["board.example.io"],
      stale_days: { price: 3, default: 400 },
      iterate: true,
      max_rounds: 3,
      min_sentences: 4,
      template_id: "memo",
      authoring: "review_thesis",
      id: "full",
      name: "Full",
      sections: ["business", "valuation"],
      depth: "standard",
      model: "gpt-6.1-sol",
      effort: "high",
      max_steps: 60,
      max_searches: 40,
      source_tiers: [1, 2],
      include_peers: true,
      valuation_methods: ["pe_ttm"],
      language: "en",
      top_holders: 3,
    },
  ],
  default_profile: "full",
  templates: [
    {
      schema_version: 1,
      id: "memo",
      name: "Memo",
      default_view: "detailed",
      sections: [
        { id: "valuation", enabled: true, detail_only: false, title: "Price and value", max_sentences: 4 },
        { id: "custom-moat-check", enabled: true, detail_only: true },
        { id: "business", enabled: false, detail_only: false },
      ],
      custom_sections: [{ id: "custom-moat-check", title: "Moat check", task: "Does the company keep its pricing power?" }],
    },
  ],
};

describe("research profiles form: planning, source policy and templates", () => {
  it("round-trips every new profile field and a template with its own section", () => {
    expect(researchProfilesToBody(researchProfilesFromBody(FULL_NEW))).toEqual(FULL_NEW);
  });

  it("leaves empty optional fields out of the body", () => {
    const p = researchProfilesToBody(researchProfilesFromBody(FULL)).profiles![0] as Record<string, unknown>;
    for (const k of ["date_horizon_days", "allow_domains", "deny_domains", "forum_domains", "stale_days", "template_id"]) {
      expect(p).not.toHaveProperty(k);
    }
    const form = researchProfilesFromBody(FULL_NEW);
    form.profiles[0].date_horizon_days = " ";
    form.profiles[0].stale_days = { price: "", default: "" };
    form.profiles[0].allow_domains = "";
    const edited = researchProfilesToBody(form).profiles![0] as Record<string, unknown>;
    expect(edited).not.toHaveProperty("date_horizon_days");
    expect(edited).not.toHaveProperty("stale_days");
    expect(edited).not.toHaveProperty("allow_domains");
  });

  it("takes domains one per line, lower-cased, without repeats", () => {
    expect(parseDomains("A.com, b.org\n\n a.com ;c.io")).toEqual(["a.com", "b.org", "c.io"]);
    expect(domainsToText(["a.com", "b.org"])).toBe("a.com\nb.org");
    expect(domainsToText(undefined)).toBe("");
  });

  it("fills new fields with the backend's defaults for an old profile", () => {
    const old = { ...FULL.profiles![1] } as Record<string, unknown>;
    for (const k of Object.keys(PROFILE_DEFAULTS)) delete old[k];
    const p = researchProfilesFromBody({ ...FULL, profiles: [old as never] }).profiles[0];
    expect([p.mode, p.web_emphasis, p.authoring, p.iterate, p.allow_forums]).toEqual(["standard", "balanced", "research_only", false, false]);
    expect([p.max_rounds, p.min_sentences, p.date_horizon_days, p.template_id]).toEqual(["1", "2", "", ""]);
  });

  it("gives a body without templates the backend's default templates", () => {
    const { templates: _dropped, ...noTemplates } = FULL_NEW;
    void _dropped;
    expect(researchProfilesFromBody(noTemplates).templates.map((t) => t.id)).toEqual(["thesis-memo", "update-note"]);
  });

  it("mirrors the backend's profile checks", () => {
    const form = researchProfilesFromBody(FULL_NEW);
    expect(validateResearchProfiles(form)).toEqual({});
    const p = form.profiles[0];
    p.date_horizon_days = "6";
    p.max_rounds = "4";
    p.min_sentences = "0";
    p.stale_days = { price: "0", search: "3651", news_item: "x", bogus: "5" };
    p.allow_domains = "example.com\nshared.com\nhttps://bad.com/x";
    p.deny_domains = "shared.com";
    p.forum_domains = "example.com";
    p.template_id = "missing";
    const errors = validateResearchProfiles(form);
    expect(errors["profiles.0.date_horizon_days"]).toEqual(["From 7 to 3650."]);
    expect(errors["profiles.0.max_rounds"]).toEqual(["From 1 to 3."]);
    expect(errors["profiles.0.min_sentences"]).toEqual(["From 1 to 6."]);
    expect(errors["profiles.0.stale_days.price"]).toEqual(["From 1 to 3650."]);
    expect(errors["profiles.0.stale_days.search"]).toEqual(["From 1 to 3650."]);
    expect(errors["profiles.0.stale_days.news_item"]).toEqual(["Type a whole number."]);
    expect(errors["profiles.0.stale_days"]?.[0]).toMatch(/bogus/);
    expect(errors["profiles.0.allow_domains"]?.join(" ")).toMatch(/https:\/\/bad\.com\/x: not a bare domain/);
    expect(errors["profiles.0.allow_domains"]?.join(" ")).toMatch(/shared\.com\. A domain cannot be both/);
    expect(errors["profiles.0.forum_domains"]?.[0]).toMatch(/example\.com/);
    expect(errors["profiles.0.template_id"]?.[0]).toMatch(/missing/);
  });

  it("limits the domain lists to 20 allowed, 40 blocked and 40 forum domains", () => {
    const many = (n: number, tld: string) => Array.from({ length: n }, (_, i) => `site${i}.${tld}`).join("\n");
    const form = researchProfilesFromBody(FULL_NEW);
    form.profiles[0].allow_domains = many(BOUNDS.allow_domains.max + 1, "com");
    form.profiles[0].deny_domains = many(BOUNDS.deny_domains.max + 1, "org");
    form.profiles[0].forum_domains = many(BOUNDS.forum_domains.max + 1, "io");
    const errors = validateResearchProfiles(form);
    expect(errors["profiles.0.allow_domains"]).toEqual(["At most 20 domains (there are 21)."]);
    expect(errors["profiles.0.deny_domains"]).toEqual(["At most 40 domains (there are 41)."]);
    expect(errors["profiles.0.forum_domains"]).toEqual(["At most 40 domains (there are 41)."]);
  });

  it("edits a template's sections: add, order, remove, owner-defined up to six", () => {
    let t = researchProfilesFromBody(FULL_NEW).templates[0];
    expect(unusedBuiltinSections(t)).not.toContain("valuation");
    t = addBuiltinSection(t, "risk");
    expect(addBuiltinSection(t, "risk").sections).toHaveLength(t.sections.length);
    expect(t.sections.map((s) => s.id)).toEqual(["valuation", "custom-moat-check", "business", "risk"]);
    t = moveTemplateSection(t, "risk", -1);
    expect(t.sections.map((s) => s.id)).toEqual(["valuation", "custom-moat-check", "risk", "business"]);
    expect(moveTemplateSection(t, "valuation", -1).sections[0].id).toBe("valuation");
    t = addCustomSection(t);
    expect(t.custom_sections.map((c) => c.id)).toEqual(["custom-moat-check", "custom-section"]);
    expect(t.sections.at(-1)?.id).toBe("custom-section");
    while (t.custom_sections.length < BOUNDS.custom_sections.max) t = addCustomSection(t);
    expect(addCustomSection(t).custom_sections).toHaveLength(6);
    t = removeTemplateSection(t, "custom-section");
    expect(t.custom_sections.map((c) => c.id)).not.toContain("custom-section");
    expect(t.sections.map((s) => s.id)).not.toContain("custom-section");
    const copy = duplicateTemplate(t, ["memo"]);
    expect(copy.id).toBe("memo-2");
    expect(copy.sections[0].key).not.toBe(t.sections[0].key);
    expect(newTemplate(["new-template"]).id).toBe("new-template-2");
  });

  it("mirrors the backend's template checks", () => {
    const form = researchProfilesFromBody(FULL_NEW);
    const t = form.templates[0];
    t.sections[0].title = "x".repeat(81);
    t.sections[0].max_sentences = "21";
    t.custom_sections[0].title = "See https://x.com";
    t.custom_sections[0].task = "too short";
    const errors = validateResearchProfiles(form);
    expect(errors["templates.0.sections.0.title"]).toEqual(["At most 80 characters."]);
    expect(errors["templates.0.sections.0.max_sentences"]).toEqual(["From 1 to 20."]);
    expect(errors["templates.0.custom_sections.0.title"]?.[0]).toMatch(/plain words/);
    expect(errors["templates.0.custom_sections.0.task"]?.[0]).toMatch(/at least 10/);

    t.custom_sections[0].title = "Moat check";
    t.custom_sections[0].task = "Is <b>this</b> a moat or not?";
    expect(validateResearchProfiles(form)["templates.0.custom_sections.0.task"]?.[0]).toMatch(/plain words/);
    t.custom_sections[0].task = "Does the company keep its pricing power?";
    t.sections[0].title = "";
    t.sections[0].max_sentences = "";
    expect(validateResearchProfiles(form)).toEqual({});

    const all = t.sections;
    t.sections = all.map((s) => ({ ...s, enabled: false }));
    expect(validateResearchProfiles(form)["templates.0.sections"]).toEqual(["Enable at least one section."]);
    t.sections = [...all, { ...all[0], key: "dup" }];
    expect(validateResearchProfiles(form)["templates.0.sections"]?.join(" ")).toMatch(/listed twice/);
    t.sections = [...all, { ...all[0], key: "unk", id: "nonsense" }];
    expect(validateResearchProfiles(form)["templates.0.sections"]?.join(" ")).toMatch(/Unknown sections: nonsense/);
    t.sections = all.filter((s) => s.id !== "custom-moat-check");
    expect(validateResearchProfiles(form)["templates.0.custom_sections"]?.[0]).toMatch(/not placed/);
    t.sections = [];
    expect(validateResearchProfiles(form)["templates.0.sections"]).toEqual(["Keep at least one section."]);
  });

  it("rejects duplicate template ids and more than six owner-defined sections", () => {
    const form = researchProfilesFromBody(FULL_NEW);
    form.templates.push({ ...form.templates[0], key: "second" });
    expect(validateResearchProfiles(form)["templates.1.id"]).toEqual(["Template 1 already uses this id."]);
    form.templates.pop();
    const custom = Array.from({ length: 7 }, (_, i) => ({ key: `c${i}`, id: `custom-s${i}`, title: "Title", task: "A plain question here.", rest: {} }));
    form.templates[0].custom_sections = custom;
    form.templates[0].sections = custom.map((c) => ({ key: c.key, id: c.id, enabled: true, title: "", max_sentences: "", detail_only: false, rest: {} }));
    form.profiles[0].template_id = "memo";
    expect(validateResearchProfiles(form)["templates.0.custom_sections"]).toEqual(["At most 6 sections of your own."]);
  });
});

/*
 * Item 6: the web copy of the default profiles and templates must match the backend's.
 * There is no committed JSON export of the backend defaults (they come from pydantic
 * default factories in apps/backend/src/steward/config/research.py, and openapi.json holds
 * no values for them), so this asserts the structural invariants against schema-typed
 * constants transcribed from that file, and, when the backend source is present, checks the
 * section list and limits against the Python text itself.
 */
describe("web default profiles and templates match the backend defaults", () => {
  const defaults = DEFAULT_RESEARCH_PROFILES;
  const profile = (id: string) => defaults.profiles!.find((p) => p.id === id)!;
  const template = (id: string) => defaults.templates!.find((t) => t.id === id)!;

  it("has the four profiles and two templates, standard as default", () => {
    expect(defaults.profiles!.map((p) => p.id)).toEqual(["standard", "quick", "deep-thesis", "earnings-update"]);
    expect(defaults.default_profile).toBe("standard");
    expect(defaults.templates!.map((t) => t.id)).toEqual(["thesis-memo", "update-note"]);
  });

  it("sets the planning, iteration and authoring defaults per profile", () => {
    expect(PROFILE_DEFAULTS).toEqual({
      mode: "standard",
      web_emphasis: "balanced",
      allow_forums: false,
      iterate: false,
      max_rounds: 1,
      min_sentences: 2,
      authoring: "research_only",
    });
    const pick = (p: ReturnType<typeof profile>) => ({
      mode: p.mode,
      web: p.web_emphasis,
      iterate: p.iterate,
      rounds: p.max_rounds,
      authoring: p.authoring,
      template: p.template_id ?? null,
      horizon: p.date_horizon_days ?? null,
      steps: [p.max_steps, p.max_searches],
      depth: p.depth,
    });
    expect(pick(profile("standard"))).toEqual({ mode: "standard", web: "balanced", iterate: false, rounds: 1, authoring: "research_only", template: null, horizon: null, steps: [60, 40], depth: "standard" });
    expect(pick(profile("quick"))).toEqual({ mode: "quick", web: "internal", iterate: false, rounds: 1, authoring: "research_only", template: null, horizon: null, steps: [20, 10], depth: "brief" });
    expect(pick(profile("deep-thesis"))).toEqual({ mode: "deep_thesis", web: "balanced", iterate: true, rounds: 2, authoring: "draft_thesis", template: "thesis-memo", horizon: null, steps: [100, 60], depth: "deep" });
    expect(pick(profile("earnings-update"))).toEqual({ mode: "earnings_update", web: "balanced", iterate: true, rounds: 1, authoring: "propose_update", template: "update-note", horizon: 120, steps: [40, 20], depth: "standard" });
  });

  it("names only built-in sections, and each profile's template presents the profile's sections", () => {
    const builtin = new Set<string>(SECTION_IDS);
    for (const p of defaults.profiles!) for (const s of p.sections) expect(builtin.has(s)).toBe(true);
    for (const t of defaults.templates!) {
      expect(t.custom_sections).toEqual([]);
      expect(t.sections.every((s) => s.enabled && builtin.has(s.id))).toBe(true);
      expect(t.default_view).toBe("concise");
    }
    for (const p of defaults.profiles!) {
      if (!p.template_id) continue;
      const t = defaults.templates!.find((x) => x.id === p.template_id);
      expect(t, `${p.id} names an existing template`).toBeDefined();
      expect(t!.sections.map((s) => s.id).sort()).toEqual([...p.sections].sort());
    }
    expect(template("update-note").sections.map((s) => s.id)).toEqual(profile("earnings-update").sections);
  });

  it("marks the thesis memo's detail-only sections", () => {
    const detailOnly = template("thesis-memo").sections.filter((s) => s.detail_only).map((s) => s.id);
    expect(detailOnly).toEqual(["financial_trend", "catalysts", "moat_risks"]);
    expect(template("update-note").sections.some((s) => s.detail_only)).toBe(false);
    expect(template("thesis-memo").sections.map((s) => s.id)).toEqual([
      "executive_summary", "business", "financial_trend", "valuation", "scenarios", "catalysts",
      "moat_risks", "counter_case", "strategy_fit", "thesis_kpis", "open_questions",
    ]);
  });

  it("passes the form's own checks and survives a round trip", () => {
    const form = researchProfilesFromBody(null);
    expect(validateResearchProfiles(form)).toEqual({});
    expect(researchProfilesToBody(form)).toEqual(defaults);
  });

  const source = path.resolve(process.cwd(), "../backend/src/steward/config/research.py");
  it.skipIf(!existsSync(source))("agrees with research.py on sections, limits and stale kinds", () => {
    const py = readFileSync(source, "utf8");
    const quoted = (block: string) => [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(quoted(/^Section = Literal\[([\s\S]*?)\]/m.exec(py)![1])).toEqual([...SECTION_IDS]);
    expect(Number(/^MAX_CUSTOM_SECTIONS = (\d+)/m.exec(py)![1])).toBe(BOUNDS.custom_sections.max);
    expect(Number(/^MAX_TEMPLATE_SECTIONS = (\d+)/m.exec(py)![1])).toBe(BOUNDS.template_sections.max);
    expect(quoted(/^STALE_KINDS: [^=]+= \(([\s\S]*?)\)/m.exec(py)![1])).toEqual(STALE_KINDS.map((k) => k.kind));
  });
});
