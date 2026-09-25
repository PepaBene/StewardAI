import type { Holding } from "@/lib/api/client";

export type HoldingsSortKey = "value" | "weight" | "ppl" | "drift" | "name";
export type SortDirection = "asc" | "desc";
export type HoldingsSort = { key: HoldingsSortKey; direction: SortDirection };

/** The Holdings list opens sorted by value, largest first. */
export const DEFAULT_HOLDINGS_SORT: HoldingsSort = { key: "value", direction: "desc" };

function sortValue(holding: Holding, key: HoldingsSortKey): number | string | null {
  switch (key) {
    case "value":
      return holding.value_base;
    case "weight":
      return holding.weight;
    case "ppl":
      return holding.ppl;
    case "drift":
      return holding.drift;
    case "name":
      return (holding.name ?? holding.ticker).toLocaleLowerCase();
  }
}

/** Sorted copy. Missing values (no target, no weight) go last in either direction;
 * ties fall back to ticker so the order is stable. */
export function sortHoldings(holdings: readonly Holding[], sort: HoldingsSort): Holding[] {
  const factor = sort.direction === "asc" ? 1 : -1;
  return [...holdings].sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    if (va === null && vb !== null) return 1;
    if (vb === null && va !== null) return -1;
    if (va !== null && vb !== null && va !== vb) return (va < vb ? -1 : 1) * factor;
    return a.ticker.localeCompare(b.ticker);
  });
}

/** Clicking a column: same column flips direction; a new column starts from its natural one. */
export function nextSort(current: HoldingsSort, key: HoldingsSortKey): HoldingsSort {
  if (current.key === key) {
    return { key, direction: current.direction === "asc" ? "desc" : "asc" };
  }
  return { key, direction: key === "name" ? "asc" : "desc" };
}
