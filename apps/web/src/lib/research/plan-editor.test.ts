import { describe, expect, it } from "vitest";

import { authorLine, isDirty, localProblems, moveQuestion, removeQuestion, rewordQuestion, toEdits, toggleGroup, type DraftQuestion } from "./plan-editor";

const q = (id: string, groups: string[] = ["filings"], web: string | null = null): DraftQuestion => ({
  id,
  section: id,
  question: `What does the evidence say about ${id}?`,
  internal_tools: [],
  web_intent: web,
  source_groups: groups,
});

describe("plan editing", () => {
  const list = [q("a"), q("b"), q("c")];

  it("reorders without going past the ends", () => {
    expect(moveQuestion(list, "b", -1).map((x) => x.id)).toEqual(["b", "a", "c"]);
    expect(moveQuestion(list, "a", -1).map((x) => x.id)).toEqual(["a", "b", "c"]);
    expect(moveQuestion(list, "c", 1).map((x) => x.id)).toEqual(["a", "b", "c"]);
  });

  it("removes and rewords one question", () => {
    expect(removeQuestion(list, "b").map((x) => x.id)).toEqual(["a", "c"]);
    expect(rewordQuestion(list, "a", "New wording here")[0]!.question).toBe("New wording here");
    expect(list[0]!.question).not.toBe("New wording here"); // the input is not mutated
  });

  it("toggles a group, and clears the search intent when web goes off", () => {
    const on = toggleGroup([q("a", ["web"], "Acme segments")], "a", "news");
    expect(on[0]!.source_groups).toEqual(["web", "news"]);
    const off = toggleGroup([q("a", ["web"], "Acme segments")], "a", "web");
    expect(off[0]!.source_groups).toEqual([]);
    expect(off[0]!.web_intent).toBeNull();
  });

  it("reports what the server would refuse", () => {
    expect(localProblems([], true)).toEqual(["A plan needs at least one question."]);
    expect(localProblems([{ ...q("a"), question: "short" }], true)[0]).toContain("10-400");
    expect(localProblems([q("a", ["web"], "x")], false)[0]).toContain("does not search");
    expect(localProblems(list, true)).toEqual([]);
  });

  it("sends the list in the order wanted, and spots an unsaved edit", () => {
    expect(toEdits(moveQuestion(list, "c", -1)).map((x) => x.id)).toEqual(["a", "c", "b"]);
    expect(isDirty(list, list)).toBe(false);
    expect(isDirty(list, removeQuestion(list, "a"))).toBe(true);
  });

  it("says who wrote the plan and why a model plan was not used", () => {
    expect(authorLine({ questions: [], authored_by: "model" })).toContain("model");
    expect(authorLine({ questions: [], fallback_reason: "the call failed" })).toContain("not used: the call failed");
    expect(authorLine({ questions: [] })).toContain("in code");
  });
});
