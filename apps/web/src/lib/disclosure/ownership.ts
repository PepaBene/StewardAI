import type { SankeyLinkInput, SankeyNodeInput } from "@/components/charts";
import type { components } from "@/lib/api/schema";

/*
 * Ownership (plan P3): types and pure logic for the lanes view. Same rules as the backend's
 * `disclosure/ownership.py`: each ownership system is its own lane and is only summed within
 * itself, there is no combined total (the lanes overlap), the part a lane does not account for is
 * shown against a stated denominator, and quarter-to-quarter movement is described, not explained.
 */

type S = components["schemas"];
export type OwnershipView = S["OwnershipOut"];
export type Lane = S["LaneOut"];
export type LaneEntry = S["LaneEntryOut"];
export type LaneKey = Lane["key"];

export const LANE_ORDER: readonly LaneKey[] = ["institutional_13f", "control_13dg", "fund_nport", "national_voting"];

export const LANE_SHORT: Record<LaneKey, string> = {
  institutional_13f: "13F managers",
  control_13dg: "13D/G stakes",
  fund_nport: "N-PORT funds",
  national_voting: "National notices",
};

/** What a lane's total means in one line, shown with the figure. */
export const LANE_CAVEAT: Record<LaneKey, string> = {
  institutional_13f: "Reported holdings, not a beneficial stake.",
  control_13dg: "A floor: one reporting person per filing.",
  fund_nport: "Inside the managers' 13F figures as well.",
  national_voting: "Voting rights, not economic interest.",
};

export const shareNumber = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export const formatShares = (v: string | null | undefined): string => {
  const n = shareNumber(v);
  return n == null ? "No share count" : new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 2 }).format(n);
};

export const formatPercent = (v: string | number | null | undefined, digits = 1): string => {
  const n = typeof v === "number" ? v : shareNumber(v);
  return n == null ? "—" : `${n.toFixed(digits)}%`;
};

/** Where the bar for a lane ends: its share of the denominator, capped for drawing, never for the label. */
export function laneBar(lane: Lane): { fill: number; over: boolean } | null {
  const pct = shareNumber(lane.percent_of_denominator);
  if (pct == null) return null;
  return { fill: Math.min(100, Math.max(0, pct)), over: pct > 100 };
}

export type HolderBook = Record<string, number>;

/**
 * Quarter-to-quarter movement among the top holders as a two-column flow. A holder in both quarters
 * is one ribbon (width = the smaller of the two figures); a holder only in one quarter flows to or
 * from "Outside the top N", marked as emphasis. The chart describes what was reported.
 */
export function rankFlowGraph(prev: HolderBook, cur: HolderBook, topN = 6): { nodes: SankeyNodeInput[]; links: SankeyLinkInput[]; moves: RankMove[] } {
  const rank = (b: HolderBook) => Object.entries(b).sort((a, c) => c[1] - a[1] || a[0].localeCompare(c[0])).map(([m]) => m);
  const pr = rank(prev);
  const cr = rank(cur);
  const top = new Set([...pr.slice(0, topN), ...cr.slice(0, topN)]);
  const nodes: SankeyNodeInput[] = [];
  const links: SankeyLinkInput[] = [];
  const moves: RankMove[] = [];
  for (const m of [...top].sort((a, b) => (cr.indexOf(a) + 1 || 99) - (cr.indexOf(b) + 1 || 99))) {
    const p = prev[m];
    const c = cur[m];
    if (p != null) nodes.push({ id: `p:${m}`, label: m, column: 0 });
    if (c != null) nodes.push({ id: `c:${m}`, label: m, column: 1 });
    if (p != null && c != null) links.push({ source: `p:${m}`, target: `c:${m}`, value: Math.min(p, c) });
    else if (p != null) links.push({ source: `p:${m}`, target: "c:out", value: p, emphasis: true });
    else if (c != null) links.push({ source: "p:out", target: `c:${m}`, value: c, emphasis: true });
    moves.push({
      manager: m,
      prevRank: p != null ? pr.indexOf(m) + 1 : null,
      curRank: c != null ? cr.indexOf(m) + 1 : null,
      prev: p ?? null,
      cur: c ?? null,
      kind: p == null ? "new" : c == null ? "exited" : c > p ? "increased" : c < p ? "decreased" : "unchanged",
    });
  }
  if (links.some((l) => l.target === "c:out")) nodes.push({ id: "c:out", label: `Outside the top ${topN}`, column: 1 });
  if (links.some((l) => l.source === "p:out")) nodes.push({ id: "p:out", label: `Outside the top ${topN}`, column: 0 });
  return { nodes, links, moves };
}

export type RankMove = {
  manager: string;
  prevRank: number | null;
  curRank: number | null;
  prev: number | null;
  cur: number | null;
  kind: "new" | "exited" | "increased" | "decreased" | "unchanged";
};

export const MOVE_LABEL: Record<RankMove["kind"], string> = {
  new: "New in the list",
  exited: "Left the list",
  increased: "Reported more",
  decreased: "Reported less",
  unchanged: "Unchanged",
};

export type StakeEvent = {
  holder: string;
  form: string;
  /** Percent of class as reported; null when the filing gives none. */
  percent: number | null;
  known_at: string;
  event_date: string | null;
  amendment: number | null;
};

/** The chain of one holder's control filings known by the cutoff, oldest first. */
export function stakesAt(events: readonly StakeEvent[], cutoffMs: number): StakeEvent[] {
  return events.filter((e) => Date.parse(e.known_at) <= cutoffMs).sort((a, b) => Date.parse(a.known_at) - Date.parse(b.known_at));
}

/** The change between consecutive reported stakes: only when both carry a percentage. */
export function stakeDeltas(chain: readonly StakeEvent[]): { at: string; delta: number | null }[] {
  return chain.slice(1).map((e, i) => {
    const p = chain[i].percent;
    return { at: e.known_at, delta: p != null && e.percent != null ? e.percent - p : null };
  });
}

/** Days from the date a figure is measured on to the date it was filed. */
export function lagLabel(days: number | null): string {
  if (days == null) return "Lag unknown";
  return days === 1 ? "1 day after" : `${days} days after`;
}
