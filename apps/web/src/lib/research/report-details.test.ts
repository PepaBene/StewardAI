import { describe, expect, it } from "vitest";

import {
  coverageFigures,
  followUpSummary,
  groupDiscoveredUrls,
  roundLine,
  stopRuleLines,
  toolCallCounts,
  toolFreshness,
  toolReason,
  toolStatusLabel,
  urlLimit,
  type DiscoveredUrl,
} from "./coverage";
import {
  balanceLines,
  claimTypeLabel,
  confidenceLabel,
  coverageNotices,
  flaggedSourceWarnings,
  groupClaimsBySection,
  nextReviewState,
  reviewLabel,
  staleClaimCount,
  tally,
  type LedgerClaim,
} from "./ledger";
import { acceptOutcome, diffLines, resolveEvidence } from "./proposal";
import { purgeSummary, shortHash, snapshotRows, sourceRoleNote, type RunSnapshot } from "./snapshot";
import { normaliseView, originalSectionIndex } from "./view";

const url = (over: Partial<DiscoveredUrl> = {}): DiscoveredUrl => ({
  url: "https://example.com/a",
  title: "A",
  host: "example.com",
  status: "cited",
  section: "business",
  round: 1,
  retrieved_at: "2026-01-01T00:00:00Z",
  ...over,
});

const claim = (over: Partial<LedgerClaim> = {}): LedgerClaim => ({
  id: "c1",
  section_id: "business",
  index: 0,
  text: "Sentence.",
  type: "fact",
  written_by: "model",
  evidence: [],
  confidence: "medium",
  why: "one source",
  stale: false,
  review: "unreviewed",
  ...over,
});

describe("view", () => {
  it("treats anything but concise as detailed", () => {
    expect(normaliseView("concise")).toBe("concise");
    expect(normaliseView("detailed")).toBe("detailed");
    expect(normaliseView(undefined)).toBe("detailed");
  });
  it("maps a displayed section to its stored position", () => {
    const full = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(originalSectionIndex(full, "c", 1)).toBe(2);
    expect(originalSectionIndex(full, "x", 1)).toBe(1);
  });
});

describe("coverage", () => {
  it("groups URLs by status in a fixed order", () => {
    const groups = groupDiscoveredUrls([url({ status: "excluded", host: "b.com" }), url({ round: 2 }), url({ round: 1 }), url({ status: "found_not_cited" })]);
    expect(groups.map((g) => [g.status, g.urls.length])).toEqual([
      ["cited", 2],
      ["found_not_cited", 1],
      ["excluded", 1],
    ]);
    expect(groups[0].urls.map((u) => u.round)).toEqual([1, 2]);
    expect(groupDiscoveredUrls(undefined).every((g) => g.urls.length === 0)).toBe(true);
  });
  it("reports the limit of a URL", () => {
    expect(urlLimit({ limitation: "paywalled", page_age: "2 days" })).toBe("paywalled");
    expect(urlLimit({ limitation: null, page_age: "2 days" })).toBe("Page age 2 days");
    expect(urlLimit({})).toBeNull();
  });
  it("counts from the summary, else from the list, else nothing", () => {
    const summary = { sources_after_first_pass: 4, sources_added_by_follow_up: 2, urls_found: 30, urls_cited: 6, urls_excluded: 9 };
    expect(coverageFigures(summary, [])).toEqual({ found: 30, cited: 6, excluded: 9 });
    expect(coverageFigures(null, [url(), url({ status: "excluded" })])).toEqual({ found: 2, cited: 1, excluded: 1 });
    expect(coverageFigures(null, undefined)).toBeNull();
  });
  it("describes rounds and the follow-up", () => {
    const round = { round: 2, sections: ["risk"], reasons: {}, sentences_added: 1, sources_added: 3, urls_found: 1, steps: 4 };
    expect(roundLine(round, (s) => s.toUpperCase())).toBe("Round 2: RISK (1 sentence, 3 sources added, 1 URL found)");
    const summary = { sources_after_first_pass: 1, sources_added_by_follow_up: 2, urls_found: 3, urls_cited: 1, urls_excluded: 0 };
    expect(followUpSummary(summary, [round], "budget used")).toEqual([
      "1 source after the first pass; follow-up added 2.",
      "1 follow-up round.",
      "Stopped because: budget used",
    ]);
    expect(followUpSummary(null, undefined, null)).toEqual([]);
  });
  it("writes the stop rule", () => {
    const lines = stopRuleLines({ max_steps: 10, max_searches: 5, max_rounds: 1, min_sentences: 2, rule: "Stop when answered." });
    expect(lines).toEqual(["Up to 10 steps, 5 searches and 1 follow-up round.", "A section counts as answered with 2 cited sentences.", "Stop when answered."]);
    expect(stopRuleLines(null)).toEqual([]);
  });
  it("summarises tool calls", () => {
    expect(toolStatusLabel("stale")).toBe("Stale");
    expect(toolStatusLabel("weird")).toBe("weird");
    expect(toolReason({ status: "skipped", note: "no key", error: null })).toBe("no key");
    expect(toolReason({ status: "ok", note: "x", error: null })).toBeNull();
    expect(toolFreshness({ figures: { ok: 3, stale: 0, missing: 1 }, as_of: "2026-02-01" })).toBe("3 ok, 1 missing, newest as of 2026-02-01");
    expect(toolFreshness({})).toBeNull();
    expect(toolCallCounts([{ status: "ok" }, { status: "skipped" }, { status: "ok" }])).toEqual([
      { status: "ok", label: "Fresh", count: 2 },
      { status: "skipped", label: "Skipped", count: 1 },
    ]);
  });
});

describe("ledger", () => {
  it("labels types, confidence and review states", () => {
    expect(claimTypeLabel("strategy_assessment")).toBe("Strategy assessment");
    expect(confidenceLabel("low")).toBe("Low confidence");
    expect(reviewLabel("owner_confirmed")).toBe("Confirmed by you");
    expect(reviewLabel("new_state")).toBe("new state");
  });
  it("toggles the owner review state", () => {
    expect(nextReviewState("unreviewed", "confirm")).toBe("owner_confirmed");
    expect(nextReviewState("owner_confirmed", "confirm")).toBe("unreviewed");
    expect(nextReviewState("owner_confirmed", "reject")).toBe("owner_rejected");
    expect(nextReviewState("machine_checked", "reject")).toBe("owner_rejected");
  });
  it("groups claims by section order and keeps unknown sections", () => {
    const groups = groupClaimsBySection(
      [claim({ id: "c2", section_id: "risk", index: 1 }), claim({ id: "c1", section_id: "business" }), claim({ id: "c3", section_id: "risk", index: 0 }), claim({ id: "c4", section_id: "extra" })],
      [{ id: "business", title: "Business" }, { id: "risk", title: "Risk" }, { id: "empty", title: "Empty" }],
    );
    expect(groups.map((g) => [g.title, g.claims.map((c) => c.id)])).toEqual([
      ["Business", ["c1"]],
      ["Risk", ["c3", "c2"]],
      ["extra", ["c4"]],
    ]);
  });
  it("tallies and counts stale claims", () => {
    expect(tally({ fact: 2, forecast: 5, unknown: 0 }, claimTypeLabel).map((t) => t.key)).toEqual(["forecast", "fact"]);
    expect(tally(undefined, String)).toEqual([]);
    expect(staleClaimCount([claim({ stale: true }), claim()])).toBe(1);
  });
  it("describes balance and coverage", () => {
    const lines = balanceLines({ has_support: true, has_challenge: false, has_unknowns: true, has_scenarios: false, missing: ["challenge"], not_requested: ["scenarios"] });
    expect(lines).toEqual(["Covered: support, unknowns.", "Missing: challenge.", "Not requested by this profile: scenarios."]);
    expect(balanceLines(null)).toEqual([]);
    expect(coverageNotices({ as_of: "2026-03-01", data_from: "2024-01-01", stale_sources: ["S2"], notices: ["Thin coverage."] })).toEqual([
      "Data from 2024-01-01 to unknown.",
      "Stale sources: S2.",
      "Thin coverage.",
    ]);
    expect(coverageNotices(undefined)).toEqual([]);
  });
  it("resolves flagged sources to titles", () => {
    expect(flaggedSourceWarnings(["S1", "S9"], [{ id: "S1", title: "A page" }])).toEqual([
      { id: "S1", title: "A page" },
      { id: "S9", title: "S9" },
    ]);
    expect(flaggedSourceWarnings(undefined, [])).toEqual([]);
  });
});

describe("snapshot and source roles", () => {
  const snapshot: RunSnapshot = {
    schema_version: 1,
    taken_at: "2026-01-01T00:00:00Z",
    profile: { id: "standard", name: "Standard workup" },
    template: null,
    layout: [],
    prompts: { research_writer: { sha256: "abcdef0123456789", text: "hello" } },
    versions: { theses: 7, strategy: null },
    thesis: null,
    plan_sha256: "p".repeat(64),
    digest: "d".repeat(64),
  };
  it("builds display rows", () => {
    const rows = snapshotRows(snapshot, "full_workup");
    expect(rows.profile).toEqual({ id: "standard", name: "Standard workup" });
    expect(rows.template).toBe("full_workup");
    expect(rows.prompts).toEqual([{ key: "research_writer", hash: "abcdef0123456789", chars: 5 }]);
    expect(rows.versions).toEqual([{ domain: "strategy", id: null }, { domain: "theses", id: 7 }]);
    expect(shortHash(rows.digest)).toHaveLength(12);
    expect(shortHash(null)).toBe("—");
  });
  it("sums a purge", () => {
    expect(purgeSummary({ urls: 1, checkpoints: 2, traces: 0 })).toBe("Removed 1 URL, 2 checkpoints and 0 tool traces.");
  });
  it("notes roles, caveats and the injection flag", () => {
    expect(sourceRoleNote({ id: "S1", role: "forum", caveat: null }, [])).toMatchObject({ roleLabel: "Forum", flagged: false });
    expect(sourceRoleNote({ id: "S1", role: "forum", caveat: null }, []).caveat).toMatch(/forum/i);
    expect(sourceRoleNote({ id: "S2", role: "owner_trusted", caveat: "Your note." }, ["S2"])).toEqual({ roleLabel: "Owner-trusted", caveat: "Your note.", flagged: true });
    expect(sourceRoleNote({ id: "S3" }, undefined)).toEqual({ roleLabel: null, caveat: null, flagged: false });
  });
});

describe("thesis proposal", () => {
  it("diffs a field line by line with evidence", () => {
    const lines = diffLines({
      before: ["old", "same"],
      after: [
        { text: "same", evidence_ids: [] },
        { text: "new", evidence_ids: ["S1"] },
      ],
    });
    expect(lines).toEqual([
      { kind: "removed", text: "old", evidenceIds: [] },
      { kind: "kept", text: "same", evidenceIds: [] },
      { kind: "added", text: "new", evidenceIds: ["S1"] },
    ]);
  });
  it("resolves evidence ids", () => {
    expect(resolveEvidence(["S1", "S2"], [{ id: "S1", title: "Filing" }])).toEqual([
      { id: "S1", title: "Filing", listed: true },
      { id: "S2", title: "S2", listed: false },
    ]);
  });
  it("turns accept answers into messages", () => {
    expect(acceptOutcome(200, null, ["why_owned"])).toEqual({ kind: "applied", message: "Saved Why owned as a new thesis version." });
    const stale = acceptOutcome(409, "The theses changed after this report was made; run the research again");
    expect(stale.kind).toBe("stale");
    expect(stale.message).toMatch(/Run the research again/);
    expect(acceptOutcome(409, "This report proposes no thesis changes")).toEqual({ kind: "error", message: "This report proposes no thesis changes" });
    expect(acceptOutcome(422, "bad").message).toBe("bad");
    expect(acceptOutcome(500, null).message).toMatch(/HTTP 500/);
  });
});
