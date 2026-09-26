import type { FeedbackOut, FeedbackTargetKind } from "@/lib/api/client";

import { latestMarks, markKey, type FeedbackTarget } from "./feedback";

/*
 * The owner's earlier marks, shown next to each item. A page can have hundreds of feedback
 * controls, so each one registers its target and the store asks `GET /feedback` once per
 * target kind for every id registered in the same tick (in chunks), instead of once per
 * control. A mark just given replaces the stored one at once.
 */

export type FeedbackFetcher = (kind: FeedbackTargetKind, ids: number[]) => Promise<FeedbackOut[]>;

/** Ids per request: keeps the query string short. The API returns at most `limit` marks. */
export const CHUNK = 80;

export type MarksStore = {
  /** Register a target; its marks are fetched with the next batch (once per id). */
  request: (target: FeedbackTarget) => void;
  /** The current mark of a target: undefined while unknown, null when there is none. */
  get: (target: FeedbackTarget) => FeedbackOut | null | undefined;
  /** Record a mark just given. */
  put: (mark: FeedbackOut) => void;
  subscribe: (listener: () => void) => () => void;
  /** Resolves when every batch queued so far has been answered (for tests). */
  flush: () => Promise<void>;
};

export function createMarksStore(
  fetcher: FeedbackFetcher,
  schedule: (run: () => void) => void = (run) => setTimeout(run, 25),
): MarksStore {
  const marks = new Map<string, FeedbackOut>();
  /** "kind:id" already fetched (or being fetched). */
  const loaded = new Set<string>();
  const pending = new Map<FeedbackTargetKind, Set<number>>();
  const listeners = new Set<() => void>();
  let scheduled = false;
  let inflight: Promise<void> = Promise.resolve();

  const notify = () => listeners.forEach((l) => l());

  async function run() {
    scheduled = false;
    const batches = [...pending].flatMap(([kind, ids]) => {
      const list = [...ids].sort((a, b) => a - b);
      const out: [FeedbackTargetKind, number[]][] = [];
      for (let i = 0; i < list.length; i += CHUNK) out.push([kind, list.slice(i, i + CHUNK)]);
      return out;
    });
    pending.clear();
    const results = await Promise.all(
      batches.map(([kind, ids]) =>
        fetcher(kind, ids).catch(() => {
          // Allow a later retry of these ids.
          ids.forEach((id) => loaded.delete(`${kind}:${id}`));
          return [] as FeedbackOut[];
        }),
      ),
    );
    for (const [key, mark] of latestMarks(results.flat())) {
      const known = marks.get(key);
      if (!known || known.created_at <= mark.created_at) marks.set(key, mark);
    }
    notify();
  }

  return {
    request(target) {
      const id = `${target.kind}:${target.id}`;
      if (loaded.has(id)) return;
      loaded.add(id);
      if (!pending.has(target.kind)) pending.set(target.kind, new Set());
      pending.get(target.kind)!.add(target.id);
      if (!scheduled) {
        scheduled = true;
        inflight = new Promise<void>((resolve) => schedule(() => void run().then(resolve)));
      }
    },
    get(target) {
      const mark = marks.get(markKey(target));
      if (mark) return mark;
      return loaded.has(`${target.kind}:${target.id}`) && !pending.get(target.kind)?.has(target.id) ? null : undefined;
    },
    put(mark) {
      marks.set(markKey(mark), mark);
      loaded.add(`${mark.target_kind}:${mark.target_id}`);
      notify();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    flush: () => inflight,
  };
}
