import type { components } from "@/lib/api/schema";

export type OwnerInputItem = components["schemas"]["OwnerInput"];
export type SupplyChainData = components["schemas"]["SupplyChain"];
export type EventImpactData = components["schemas"]["EventImpact"];
export type EventPathData = components["schemas"]["EventPath"];

export const CONFIDENCE_LABEL: Record<EventPathData["confidence"], string> = {
  high: "High: every link is stated and recent",
  medium: "Medium: a link has no stated share",
  low: "Low: a link is more than three years old",
};
export const SEVERITY_LABEL: Record<EventPathData["severity"], string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  none: "None",
};
export const EVENT_DIRECTIONS: readonly { id: "downstream" | "upstream"; label: string }[] = [
  { id: "downstream", label: "A supplier is disrupted (follows its customers)" },
  { id: "upstream", label: "A customer is lost (follows its suppliers)" },
];
export const EVENT_STATES: readonly { id: "confirmed" | "reported" | "rumoured"; label: string }[] = [
  { id: "confirmed", label: "Confirmed" },
  { id: "reported", label: "Reported, not confirmed" },
  { id: "rumoured", label: "Rumour" },
];

/** The path as a sentence: A supplies B, B supplies C. */
export function pathLabel(path: EventPathData): string {
  return [path.start, ...path.edges.map((e) => (path.edges[0].supplier === path.start ? e.customer : e.supplier))].join(" → ");
}

export type IndexComparisonsData =components["schemas"]["IndexComparisons"];
export type IndexComparisonData = components["schemas"]["IndexComparison"];
export type FundRelativeData = components["schemas"]["FundRelative"];
export type RegionSplitBody = components["schemas"]["RegionSplitRequest"];
export type SupplyEdgeBody = components["schemas"]["SupplyEdgeRequest"];
export type IndexWeightsBody = components["schemas"]["IndexWeightsRequest"];

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

function percent(raw: string): number | null {
  const n = Number(raw.replace("%", "").replace(",", ".").trim());
  return raw.trim() !== "" && Number.isFinite(n) ? n : null;
}

/** "Germany 40" or "FR: 30%" per line -> country -> share of the region (a fraction). The
 * countries are checked by the server; here only the shape and the total. */
export function parseRegionLines(text: string): Parsed<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const line of text.split(/\r?\n/)) {
    if (line.trim() === "") continue;
    const m = /^(.*?)[\s:=]+(-?[\d.,]+\s*%?)\s*$/.exec(line.trim());
    const share = m ? percent(m[2]) : null;
    if (!m || m[1].trim() === "" || share === null || share <= 0 || share > 100) {
      return { ok: false, error: `Cannot read “${line.trim()}”. Write a country and a percent, such as “Germany 40”.` };
    }
    const country = m[1].trim();
    out[country] = (out[country] ?? 0) + share / 100;
  }
  const total = Object.values(out).reduce((a, b) => a + b, 0);
  if (Object.keys(out).length === 0) return { ok: false, error: "Add at least one country." };
  if (Math.abs(total - 1) > 0.005) return { ok: false, error: `The percents add up to ${(total * 100).toFixed(1)}, not 100.` };
  return { ok: true, value: out };
}

/** One constituent per line, `Name ; ISIN ; Ticker ; Weight %` (a tab works as the separator, so a
 * row pasted from a spreadsheet works). ISIN and ticker may be left empty. */
export function parseConstituents(text: string): Parsed<IndexWeightsBody["constituents"]> {
  const rows: IndexWeightsBody["constituents"] = [];
  for (const line of text.split(/\r?\n/)) {
    if (line.trim() === "") continue;
    const cells = line.split(/\t|;/).map((c) => c.trim());
    if (cells.length < 2) {
      return { ok: false, error: `Cannot read “${line.trim()}”. Write name ; ISIN ; ticker ; weight %.` };
    }
    const weight = percent(cells[cells.length - 1]);
    const [name, isin = "", ticker = ""] = cells.slice(0, -1);
    if (name === "" || weight === null || weight <= 0 || weight > 100) {
      return { ok: false, error: `Cannot read “${line.trim()}”. The last column is the weight in percent.` };
    }
    if (isin !== "" && !ISIN.test(isin.toUpperCase())) {
      return { ok: false, error: `“${isin}” is not an ISIN (two letters, nine letters or digits, one digit).` };
    }
    rows.push({ name, isin: isin === "" ? null : isin.toUpperCase(), ticker: ticker === "" ? null : ticker, weight: weight / 100 });
  }
  if (rows.length === 0) return { ok: false, error: "Add at least one constituent." };
  const total = rows.reduce((a, r) => a + r.weight, 0);
  if (total > 1.005) return { ok: false, error: `The weights add up to ${(total * 100).toFixed(1)}%, which is more than 100.` };
  return { ok: true, value: rows };
}

export type NodePos = { symbol: string; x: number; y: number };

export const GRAPH = { colWidth: 190, rowHeight: 58, nodeW: 132, nodeH: 38, pad: 16 } as const;

/** Columns by level (suppliers on the left), rows in the order given. */
export function layoutGraph(nodes: SupplyChainData["nodes"]): { positions: Map<string, NodePos>; width: number; height: number } {
  const byLevel = new Map<number, string[]>();
  for (const n of nodes) byLevel.set(n.level, [...(byLevel.get(n.level) ?? []), n.symbol]);
  const positions = new Map<string, NodePos>();
  let maxRows = 0;
  for (const [level, symbols] of byLevel) {
    maxRows = Math.max(maxRows, symbols.length);
    symbols.forEach((symbol, row) => {
      positions.set(symbol, { symbol, x: GRAPH.pad + level * GRAPH.colWidth, y: GRAPH.pad + row * GRAPH.rowHeight });
    });
  }
  const levels = byLevel.size === 0 ? 0 : Math.max(...byLevel.keys()) + 1;
  return {
    positions,
    width: GRAPH.pad * 2 + Math.max(levels - 1, 0) * GRAPH.colWidth + GRAPH.nodeW,
    height: GRAPH.pad * 2 + Math.max(maxRows - 1, 0) * GRAPH.rowHeight + GRAPH.nodeH,
  };
}
