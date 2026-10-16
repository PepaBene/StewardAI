import type { components } from "@/lib/api/schema";
import type { CheckState } from "@/lib/insight/insight";

/*
 * The engine's guardrails as check rows (plan §5.12): the bar a stricter confidence floor must
 * clear, said once in words, then one row per decision kind with its reviewed precision against
 * the target. A kind with fewer reviewed decisions than the gate asks for is "could not be
 * measured", never a pass or a fail.
 */

type Gates = components["schemas"]["GateOut"];
type KindPrecision = components["schemas"]["KindPrecision"];

export type GateRow = { id: string; state: CheckState; lead: string; sentence: string };

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** "Engine_change" → "Engine change". */
export function kindName(kind: string): string {
  const s = kind.replaceAll("_", " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : kind;
}

/** The floor gate in one sentence. */
export function gateRule(g: Gates): string {
  const change = g.max_wrong_rate_increase;
  const points = Math.round(Math.abs(change) * 100);
  const wrong =
    change < 0
      ? `make wrong decisions at least ${points} ${points === 1 ? "point" : "points"} rarer`
      : change === 0
        ? "not make wrong decisions more common"
        : `not raise the share of wrong decisions by more than ${points} ${points === 1 ? "point" : "points"}`;
  return `Before the engine raises a confidence floor, it replays at least ${g.min_n} past decisions, ${g.min_reviewed} of them checked by you or a second model, and the stricter floor must ${wrong}.`;
}

/** One row per decision kind, judged only once the gate's reviewed minimum is reached. */
export function precisionRows(kinds: readonly KindPrecision[], target: number, g: Gates | null): GateRow[] {
  const floor = g?.min_reviewed ?? 0;
  return kinds.map((k) => {
    const lead = kindName(k.kind);
    if (k.reviewed < floor || !k.enough_evidence || k.precision === null) {
      const need = Math.max(0, floor - k.reviewed);
      return {
        id: k.kind,
        state: "unknown",
        lead,
        sentence: need > 0 ? `${k.reviewed} reviewed so far; ${need} more before it counts.` : `${k.reviewed} reviewed, not enough to judge yet.`,
      };
    }
    const met = k.met ?? k.precision >= target;
    return {
      id: k.kind,
      state: met ? "pass" : "fail",
      lead,
      sentence: `${pct(k.precision)} right (${k.right} of ${k.reviewed} reviewed), target ${pct(target)}.`,
    };
  });
}
