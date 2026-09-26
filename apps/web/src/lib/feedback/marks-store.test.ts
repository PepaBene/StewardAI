import { describe, expect, it } from "vitest";

import type { FeedbackOut, FeedbackTargetKind } from "@/lib/api/client";

import { CHUNK, createMarksStore } from "./marks-store";

const mark = (over: Partial<FeedbackOut>): FeedbackOut => ({
  id: 1,
  target_kind: "fact",
  target_id: 1,
  sentence_path: null,
  rating: "useful",
  note: null,
  source_hosts: [],
  effects: {},
  created_at: "2026-09-25T10:00:00Z",
  ...over,
});

function setup(answer: (kind: FeedbackTargetKind, ids: number[]) => FeedbackOut[]) {
  const calls: [FeedbackTargetKind, number[]][] = [];
  const queue: (() => void)[] = [];
  const store = createMarksStore(
    async (kind, ids) => {
      calls.push([kind, ids]);
      return answer(kind, ids);
    },
    (run) => queue.push(run),
  );
  const tick = async () => {
    queue.splice(0).forEach((run) => run());
    await store.flush();
  };
  return { store, calls, tick };
}

describe("feedback marks store", () => {
  it("batches every control registered in one tick into one request per kind", async () => {
    const { store, calls, tick } = setup((kind, ids) =>
      kind === "fact" ? ids.filter((id) => id === 2).map((id) => mark({ id: 9, target_id: id, rating: "wrong" })) : [],
    );
    store.request({ kind: "fact", id: 3 });
    store.request({ kind: "fact", id: 2 });
    store.request({ kind: "fact", id: 2 });
    store.request({ kind: "story", id: 5 });
    expect(store.get({ kind: "fact", id: 2 })).toBeUndefined();
    await tick();
    expect(calls).toEqual([
      ["fact", [2, 3]],
      ["story", [5]],
    ]);
    expect(store.get({ kind: "fact", id: 2 })?.rating).toBe("wrong");
    expect(store.get({ kind: "fact", id: 3 })).toBeNull();
    // Already loaded: no second request.
    store.request({ kind: "fact", id: 2 });
    await tick();
    expect(calls).toHaveLength(2);
  });

  it("splits long id lists into chunks", async () => {
    const { store, calls, tick } = setup(() => []);
    for (let id = 1; id <= CHUNK + 5; id++) store.request({ kind: "news_item", id });
    await tick();
    expect(calls.map(([, ids]) => ids.length)).toEqual([CHUNK, 5]);
  });

  it("keeps report sentences apart by path and shows a new mark at once", async () => {
    const { store, tick } = setup(() => [
      mark({ id: 1, target_kind: "report_sentence", target_id: 7, sentence_path: "overview/0", rating: "useful" }),
    ]);
    const listener = { calls: 0 };
    store.subscribe(() => listener.calls++);
    store.request({ kind: "report_sentence", id: 7, sentencePath: "overview/0" });
    store.request({ kind: "report_sentence", id: 7, sentencePath: "overview/1" });
    await tick();
    expect(store.get({ kind: "report_sentence", id: 7, sentencePath: "overview/0" })?.rating).toBe("useful");
    expect(store.get({ kind: "report_sentence", id: 7, sentencePath: "overview/1" })).toBeNull();
    store.put(mark({ id: 2, target_kind: "report_sentence", target_id: 7, sentence_path: "overview/1", rating: "wrong", created_at: "2026-09-25T11:00:00Z" }));
    expect(store.get({ kind: "report_sentence", id: 7, sentencePath: "overview/1" })?.rating).toBe("wrong");
    expect(listener.calls).toBe(2);
  });

  it("retries ids whose request failed", async () => {
    let fail = true;
    const calls: number[][] = [];
    const queue: (() => void)[] = [];
    const store = createMarksStore(
      async (_kind, ids) => {
        calls.push(ids);
        if (fail) throw new Error("offline");
        return [];
      },
      (run) => queue.push(run),
    );
    store.request({ kind: "alert", id: 1 });
    queue.splice(0).forEach((r) => r());
    await store.flush();
    fail = false;
    store.request({ kind: "alert", id: 1 });
    queue.splice(0).forEach((r) => r());
    await store.flush();
    expect(calls).toEqual([[1], [1]]);
  });
});
