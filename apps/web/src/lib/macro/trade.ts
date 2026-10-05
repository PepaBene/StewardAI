/**
 * Trade-flow view model for one mapped product exposure (`trade_exposure` in
 * `/holdings/{ticker}/data-context`). National trade rows say nothing about the issuer's own
 * customers or suppliers, so the diagram only shows how the country's trade in that product
 * is split by partner. The World row is a total, never a partner, and is kept out of the flows.
 */
import type { components } from "@/lib/api/schema";
import type { SankeyLinkInput, SankeyNodeInput } from "@/lib/charts/sankey";
import { toNumber } from "@/lib/reference/peers";

export type TradeExposure = components["schemas"]["TradeExposure"];
export type TradeRow = components["schemas"]["TradeRow"];

export type TradeFlow = "export" | "import";
export const FLOW_LABEL: Record<TradeFlow, string> = { export: "Exports", import: "Imports" };

const MAX_PARTNERS = 8;

export function isWorld(row: Pick<TradeRow, "partner" | "partner_iso">): boolean {
  const p = String(row.partner).trim();
  return p === "0" || p === "W00" || p === "WLD" || row.partner_iso === "W00";
}

export type TradeDiagram = {
  flow: TradeFlow;
  reporter: string;
  nodes: SankeyNodeInput[];
  links: SankeyLinkInput[];
  /** Partners drawn, largest first. */
  shown: { partner: string; value: number }[];
  /** Partners with a value that did not fit; they stay in the table, never merged into "other". */
  hidden: number;
  /** The World total when the source reports one, null otherwise. */
  worldTotal: number | null;
  /** Rows with no value (null, not zero). */
  unreported: number;
};

export function availableFlows(rows: readonly TradeRow[]): TradeFlow[] {
  return (["export", "import"] as const).filter((f) => rows.some((r) => r.flow === f));
}

export function tradeDiagram(rows: readonly TradeRow[], flow: TradeFlow): TradeDiagram | null {
  const ofFlow = rows.filter((r) => r.flow === flow);
  if (ofFlow.length === 0) return null;
  const reporter = ofFlow[0].reporter_iso ?? ofFlow[0].reporter;
  let worldTotal: number | null = null;
  let unreported = 0;
  const partners: { partner: string; value: number }[] = [];
  for (const r of ofFlow) {
    const value = toNumber(r.trade_value);
    if (isWorld(r)) {
      worldTotal = value;
      continue;
    }
    if (value === null) {
      unreported += 1;
      continue;
    }
    if (value > 0) partners.push({ partner: r.partner_iso ?? r.partner, value });
  }
  partners.sort((a, b) => b.value - a.value || a.partner.localeCompare(b.partner));
  const shown = partners.slice(0, MAX_PARTNERS);
  if (shown.length === 0) return null;
  const home: SankeyNodeInput = { id: "reporter", label: reporter, column: flow === "export" ? 0 : 1 };
  const others: SankeyNodeInput[] = shown.map((p, i) => ({ id: `p${i}`, label: p.partner, column: flow === "export" ? 1 : 0 }));
  const links: SankeyLinkInput[] = shown.map((p, i) =>
    flow === "export" ? { source: "reporter", target: `p${i}`, value: p.value } : { source: `p${i}`, target: "reporter", value: p.value },
  );
  return {
    flow,
    reporter,
    nodes: flow === "export" ? [home, ...others] : [...others, home],
    links,
    shown,
    hidden: partners.length - shown.length,
    worldTotal,
    unreported,
  };
}

/** Largest partner's share of the drawn partners, null with fewer than two partners. */
export function topPartnerShare(d: TradeDiagram): number | null {
  if (d.shown.length < 2) return null;
  const total = d.shown.reduce((s, p) => s + p.value, 0);
  return total > 0 ? d.shown[0].value / total : null;
}
