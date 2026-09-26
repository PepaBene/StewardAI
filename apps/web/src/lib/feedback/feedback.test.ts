import { describe, expect, it } from "vitest";

import type { FeedbackOut } from "@/lib/api/client";

import {
  buildFeedback,
  canAddToEval,
  describeFeedbackFailure,
  digestOverviewPath,
  digestSectionPath,
  effectParts,
  emptyDraft,
  latestMarks,
  markKey,
  NOTE_MAX,
  noteSentencePath,
  summarizeEffects,
} from "./feedback";

const mark = (over: Partial<FeedbackOut>): FeedbackOut => ({
  id: 1,
  target_kind: "fact",
  target_id: 10,
  sentence_path: null,
  rating: "useful",
  note: null,
  source_hosts: [],
  effects: {},
  created_at: "2026-09-25T10:00:00Z",
  ...over,
});

describe("sentence paths", () => {
  it("addresses digest and impact-note sentences by their index in the stored body", () => {
    expect(digestOverviewPath(0)).toBe("overview/0");
    expect(digestSectionPath(2, 1)).toBe("sections/2/summary/1");
    expect(noteSentencePath(3)).toBe("sentences/3");
    expect(() => digestOverviewPath(-1)).toThrow();
    expect(() => noteSentencePath(1.5)).toThrow();
  });

  it("keys a target with its path", () => {
    expect(markKey({ kind: "fact", id: 12 })).toBe("fact:12");
    expect(markKey({ kind: "report_sentence", id: 7, sentencePath: "overview/0" })).toBe("report_sentence:7:overview/0");
    expect(markKey(mark({ target_kind: "report_sentence", target_id: 7, sentence_path: "overview/0" }))).toBe(
      "report_sentence:7:overview/0",
    );
  });
});

describe("current marks", () => {
  it("keeps the newest mark per target, sentence by sentence", () => {
    const marks = latestMarks([
      mark({ id: 1, rating: "useful", created_at: "2026-09-25T10:00:00Z" }),
      mark({ id: 3, rating: "wrong", created_at: "2026-09-25T12:00:00Z" }),
      mark({ id: 2, rating: "irrelevant", created_at: "2026-09-25T11:00:00Z" }),
      mark({ id: 4, target_kind: "report_sentence", target_id: 7, sentence_path: "overview/0", rating: "useful" }),
      mark({ id: 5, target_kind: "report_sentence", target_id: 7, sentence_path: "overview/1", rating: "wrong" }),
    ]);
    expect(marks.get("fact:10")?.rating).toBe("wrong");
    expect(marks.get("report_sentence:7:overview/0")?.rating).toBe("useful");
    expect(marks.get("report_sentence:7:overview/1")?.rating).toBe("wrong");
    expect(marks.size).toBe(3);
  });
});

describe("the request", () => {
  it("sends a plain mark with an optional trimmed note", () => {
    const r = buildFeedback({ kind: "verdict", id: 4 }, { ...emptyDraft("irrelevant"), note: "  old period  " });
    expect(r).toEqual({ ok: true, body: { target_kind: "verdict", target_id: 4, rating: "irrelevant", add_to_eval: false, note: "old period" } });
  });

  it("requires a rating, a note within the limit and a sentence path only for report sentences", () => {
    const none = buildFeedback({ kind: "fact", id: 1 }, emptyDraft());
    expect(none.ok).toBe(false);
    if (!none.ok) expect(none.errors.rating).toBeTruthy();
    const long = buildFeedback({ kind: "fact", id: 1 }, { ...emptyDraft("wrong"), note: "x".repeat(NOTE_MAX + 1) });
    expect(long.ok ? null : long.errors.note).toMatch(/1000 characters/);
    expect(buildFeedback({ kind: "report_sentence", id: 7 }, emptyDraft("wrong")).ok).toBe(false);
    expect(buildFeedback({ kind: "report_sentence", id: 7, sentencePath: "Overview/0" }, emptyDraft("wrong")).ok).toBe(false);
    expect(buildFeedback({ kind: "fact", id: 1, sentencePath: "overview/0" }, emptyDraft("wrong")).ok).toBe(false);
    expect(buildFeedback({ kind: "report_sentence", id: 7, sentencePath: "sections/1/summary/0" }, emptyDraft("wrong"))).toEqual({
      ok: true,
      body: { target_kind: "report_sentence", target_id: 7, sentence_path: "sections/1/summary/0", rating: "wrong", add_to_eval: false },
    });
  });

  it("offers the eval set only for a wrong fact or news item", () => {
    expect(canAddToEval("fact", "wrong")).toBe(true);
    expect(canAddToEval("news_item", "wrong")).toBe(true);
    expect(canAddToEval("fact", "useful")).toBe(false);
    expect(canAddToEval("story", "wrong")).toBe(false);
    // A stale tick is ignored when the rating no longer allows it.
    const r = buildFeedback({ kind: "fact", id: 1 }, { ...emptyDraft("useful"), addToEval: true });
    expect(r.ok && r.body.add_to_eval).toBe(false);
  });

  it("adds a wrong fact to the golden set with its corrected value as printed", () => {
    const draft = { ...emptyDraft("wrong"), addToEval: true, correctedValue: "94,036.50", correctedValueAsWritten: " 94,036.5 ", correctedSnippet: "Revenue was 94,036.5" };
    expect(buildFeedback({ kind: "fact", id: 12 }, draft)).toEqual({
      ok: true,
      body: {
        target_kind: "fact",
        target_id: 12,
        rating: "wrong",
        add_to_eval: true,
        corrected_value: "94036.5",
        corrected_value_as_written: "94,036.5",
        corrected_snippet: "Revenue was 94,036.5",
      },
    });
    const missing = buildFeedback({ kind: "fact", id: 12 }, { ...draft, correctedValue: "" });
    expect(missing.ok ? null : missing.errors.correctedValue).toMatch(/right value/);
    const bad = buildFeedback({ kind: "fact", id: 12 }, { ...draft, correctedValue: "about 94k", correctedValueAsWritten: "x".repeat(41) });
    expect(bad.ok ? null : bad.errors).toMatchObject({ correctedValue: expect.stringMatching(/number/), correctedValueAsWritten: expect.any(String) });
  });

  it("adds a wrong news item to the labelled set with its corrected label", () => {
    const draft = { ...emptyDraft("wrong"), addToEval: true, label: { eventType: "m_and_a" as const, affectedTicker: " AAPL_US_EQ ", relevant: true, rumour: true } };
    const r = buildFeedback({ kind: "news_item", id: 5 }, draft);
    expect(r.ok && r.body.corrected_label).toEqual({ event_type: "m_and_a", affected_ticker: "AAPL_US_EQ", relevant: true, rumour: true });
    const none = buildFeedback({ kind: "news_item", id: 5 }, { ...draft, label: { ...draft.label, eventType: "", affectedTicker: "" } });
    expect(none.ok ? null : none.errors.eventType).toBeTruthy();
    const noTicker = buildFeedback({ kind: "news_item", id: 5 }, { ...draft, label: { ...draft.label, affectedTicker: "" } });
    expect(noTicker.ok && noTicker.body.corrected_label?.affected_ticker).toBeNull();
    const badTicker = buildFeedback({ kind: "news_item", id: 5 }, { ...draft, label: { ...draft.label, affectedTicker: "not a ticker!" } });
    expect(badTicker.ok ? null : badTicker.errors.affectedTicker).toBeTruthy();
  });

  it("words the API's refusals", () => {
    expect(describeFeedbackFailure(404, { detail: "No fact 9" })).toBe("Not found: No fact 9");
    expect(describeFeedbackFailure(422, { detail: [{ msg: "Value error, only facts" }] })).toMatch(/^Not accepted: /);
    expect(describeFeedbackFailure(500, null)).toBe("Not saved (HTTP 500).");
    expect(describeFeedbackFailure(0, { detail: "offline" })).toBe("Not sent: offline");
  });
});

describe("effects", () => {
  it("words what a wrong fact did", () => {
    const effects = { quarantined: true, verdict_job_id: 42, trust: { "ir.test": 0.8, "b.example": 0.95 }, eval_candidate_id: 7 };
    expect(summarizeEffects(effects)).toBe(
      "Quarantined; verdicts re-judged (job #42); b.example trust 0.95; ir.test trust 0.8; added to the eval set (candidate #7)",
    );
    expect(effectParts(effects)[1]).toEqual({ text: "verdicts re-judged (job #42)", href: "/jobs/42" });
  });

  it("says when nothing changed, and when the item was already in the eval set", () => {
    expect(summarizeEffects({})).toBeNull();
    expect(summarizeEffects(null)).toBeNull();
    expect(summarizeEffects({ trust: { "ir.test": 0.6000000001 } })).toBe("ir.test trust 0.6");
    expect(summarizeEffects({ eval_candidate_id: null })).toBe("already in the eval set");
  });
});
