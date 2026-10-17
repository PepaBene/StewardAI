import type { Verdict } from "@/components/insight/verdict-line";
import type { ChangeItem } from "@/lib/insights/insights";

/*
 * "What changed" in words: one deterministic sentence per area saying which changes matter
 * and what they usually mean, so the feed's before/after strings are read, not just listed.
 */

export type VerdictResult = { tone: Verdict; text: string };

type Item = Pick<ChangeItem, "area" | "kind" | "subject" | "what" | "before" | "after">;
type Named = Item & { name?: string };

/** A move of at least this share is worth reading; smaller estimate moves are routine drift. */
export const MATERIAL_MOVE = 0.02;

/** The first number in a feed value: "20.61", "4.979e+11", "+178", "-274,036", "2.03%". */
export function parseValue(text: string | null | undefined): number | null {
  if (!text) return null;
  const m = text.replace(/,/g, "").match(/[-+]?\d*\.?\d+(?:e[-+]?\d+)?/i);
  return m ? Number(m[0]) : null;
}

/** The relative change from before to after; null when either side is missing or zero. */
export function relativeChange(item: Pick<Item, "before" | "after">): number | null {
  const b = parseValue(item.before);
  const a = parseValue(item.after);
  if (b == null || a == null || b === 0) return null;
  return a / b - 1;
}

/** Managers in a 13F value: "5,405 managers, 4,347,147,316 shares (2026-03-31)" gives 5405. */
export function managers(text: string | null | undefined): number | null {
  const m = text?.replace(/,/g, "").match(/(\d+) managers/);
  return m ? Number(m[1]) : null;
}

function pct(x: number, digits = 1): string {
  return `${x > 0 ? "+" : x < 0 ? "−" : "±"}${(Math.abs(x) * 100).toFixed(digits)}%`;
}

function label(i: Named): string {
  return i.name || i.subject;
}

function list(names: string[], max = 3): string {
  const shown = names.slice(0, max);
  const rest = names.length - shown.length;
  const head = shown.length > 1 ? `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}` : (shown[0] ?? "");
  return rest > 0 ? `${shown.join(", ")} and ${rest} more` : head;
}

/** Estimates: how many moved materially, which way, and the largest. */
export function estimatesVerdict(items: readonly Named[]): VerdictResult {
  const moves = items.map((i) => ({ i, c: relativeChange(i) })).filter((m): m is { i: Named; c: number } => m.c != null);
  const material = moves.filter((m) => Math.abs(m.c) >= MATERIAL_MOVE).sort((a, b) => Math.abs(b.c) - Math.abs(a.c));
  if (material.length === 0) {
    return {
      tone: "neutral",
      text: `${items.length} analyst estimates moved, none by ${MATERIAL_MOVE * 100}% or more: routine drift, nothing to act on.`,
    };
  }
  const up = material.filter((m) => m.c > 0).length;
  const top = material.slice(0, 3).map((m) => `${label(m.i)} ${m.i.what.replace(/^\w+ /, "")} ${pct(m.c)}`);
  return {
    tone: up >= material.length - up ? "good" : "caution",
    text: `${material.length} of ${items.length} estimate changes moved ${MATERIAL_MOVE * 100}% or more (${up} up, ${material.length - up} down); the largest: ${top.join("; ")}. Rising estimates after a result are one of the strategy's measured edges (principle 7b); the rest is routine drift.`,
  };
}

/** Insiders: where the last 90 days were net buying, the stronger signal. */
export function insidersVerdict(items: readonly Named[]): VerdictResult {
  const recent = items.map((i) => ({ i, v: parseValue(i.after) ?? 0 }));
  const buying = recent.filter((r) => r.v > 0);
  const selling = recent.filter((r) => r.v < 0);
  const text =
    `Over the last 90 days insiders were net sellers at ${selling.length} of ${items.length} holdings and net buyers at ${buying.length}` +
    (buying.length ? ` (${list(buying.map((b) => label(b.i)))})` : "") +
    ". Open-market selling is routine (pay, taxes, diversification); buying, above all by two or more insiders within 90 days, is the signal the strategy counts (principle 7b).";
  return { tone: buying.length ? "good" : "neutral", text };
}

/** 13F: where the number of institutional holders rose or fell by more than 5%. */
export function ownershipVerdict(items: readonly Named[]): VerdictResult {
  const moves = items
    .map((i) => {
      const b = managers(i.before);
      const a = managers(i.after);
      return { i, b, a, c: b && a ? a / b - 1 : null };
    })
    .filter((m): m is { i: Named; b: number; a: number; c: number } => m.c != null);
  const rising = moves.filter((m) => m.c >= 0.05).sort((x, y) => y.c - x.c);
  const falling = moves.filter((m) => m.c <= -0.05).sort((x, y) => x.c - y.c);
  const parts = [`Between the two latest 13F quarters, institutional holders rose by 5% or more at ${rising.length} holdings`];
  if (rising.length) parts[0] += ` (${list(rising.map((m) => `${label(m.i)} ${pct(m.c, 0)}`))})`;
  parts.push(`fell at ${falling.length}${falling.length ? ` (${list(falling.map((m) => `${label(m.i)} ${pct(m.c, 0)}`))})` : ""}`);
  return {
    tone: falling.length > rising.length ? "caution" : "neutral",
    text: `${parts.join(" and ")}. 13F data are 45 days late and cover large managers only: a trend to watch, not a trigger.`,
  };
}

/** The sentence for one area of the feed. */
export function areaVerdict(area: ChangeItem["area"], items: readonly Named[]): VerdictResult {
  if (area === "estimates") return estimatesVerdict(items);
  if (area === "insiders") return insidersVerdict(items);
  if (area === "ownership") return ownershipVerdict(items);
  if (area === "fund_holdings") {
    return {
      tone: "neutral",
      text: `${items.length} line${items.length === 1 ? "" : "s"} changed inside your funds' published holdings. A fund's own trading changes what you own through it, not a decision of yours.`,
    };
  }
  if (area === "fundamentals") {
    return {
      tone: "caution",
      text: `${items.length} filed figure${items.length === 1 ? " was" : "s were"} revised for a period already reported: check whether a thesis KPI rests on one of them.`,
    };
  }
  if (area === "graph") {
    return { tone: "neutral", text: `${items.length} company link${items.length === 1 ? "" : "s"} (parents, subsidiaries, suppliers) changed status.` };
  }
  if (area === "price") return { tone: "neutral", text: `${items.length} holding${items.length === 1 ? "" : "s"} moved 3% or more between the two snapshots.` };
  return { tone: "neutral", text: `${items.length} change${items.length === 1 ? "" : "s"}.` };
}

/** Items in the order worth reading: the largest relative move first where values are numbers. */
export function readingOrder<T extends Item>(area: ChangeItem["area"], items: readonly T[]): T[] {
  if (area !== "estimates" && area !== "fund_holdings") return [...items];
  return [...items].sort((a, b) => Math.abs(relativeChange(b) ?? 0) - Math.abs(relativeChange(a) ?? 0));
}
