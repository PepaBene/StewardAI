import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type ChangeFeed = S["ChangeFeed"];
export type ChangeItem = S["ChangeItem"];
export type SectorMap = S["SectorMap"];
export type GroupStats = S["GroupStats"];
export type Spread = S["Spread"];

export const AREA_LABEL: Record<ChangeItem["area"], string> = {
  fundamentals: "Filed figures",
  estimates: "Estimates",
  price: "Price",
  fund_holdings: "Fund holdings",
  graph: "Company links",
  macro: "Macro series",
};

export const KIND_LABEL: Record<ChangeItem["kind"], string> = {
  new: "New",
  revised: "Revised",
  moved: "Moved",
  added: "Added",
  removed: "Removed",
  dropped: "Dropped",
};

/** Groups the feed by area, keeping the feed's order inside each group. */
export function groupByArea(items: readonly ChangeItem[]): [ChangeItem["area"], ChangeItem[]][] {
  const out = new Map<ChangeItem["area"], ChangeItem[]>();
  for (const item of items) out.set(item.area, [...(out.get(item.area) ?? []), item]);
  return [...out.entries()];
}

/** "median 12% (5 of 8)", or the range and why there is no median when the sample is thin. */
export function spreadLabel(s: Spread, fmt: (n: number) => string): string {
  if (s.n === 0) return `none of ${s.of} have a value`;
  const base = `${s.n} of ${s.of}`;
  if (s.median === null || s.median === undefined) {
    const range = s.low === s.high ? fmt(s.low as number) : `${fmt(s.low as number)} to ${fmt(s.high as number)}`;
    return `too few for a median (${base}): ${range}`;
  }
  return `median ${fmt(s.median)} (${base}), range ${fmt(s.low as number)} to ${fmt(s.high as number)}`;
}
