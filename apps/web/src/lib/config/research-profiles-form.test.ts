import { describe, expect, it } from "vitest";

import type { ResearchProfilesConfig } from "@/lib/api/client";
import { SECTION_IDS } from "@/lib/research/report";

import {
  DEFAULT_RESEARCH_PROFILES,
  duplicateProfile,
  moveInList,
  newProfile,
  researchProfilesFromBody,
  researchProfilesToBody,
  toggleInList,
  uniqueId,
  validateResearchProfiles,
} from "./research-profiles-form";

const FULL: ResearchProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      id: "deep-dive",
      name: "Deep dive",
      sections: [...SECTION_IDS].reverse(),
      depth: "deep",
      model: "claude-opus-5-5",
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
      id: "screen",
      name: "Screen",
      sections: ["valuation"],
      depth: "brief",
      model: "claude-haiku-4-5",
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
};

describe("research profiles form", () => {
  it("round-trips every field of a fully populated body", () => {
    expect(researchProfilesToBody(researchProfilesFromBody(FULL))).toEqual(FULL);
  });

  it("starts from the backend defaults (standard, quick) when nothing is saved", () => {
    const form = researchProfilesFromBody(null);
    expect(form.profiles.map((p) => p.id)).toEqual(["standard", "quick"]);
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
    form.profiles[0].max_steps = "61";
    form.profiles[1].model = "gpt-5";
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
