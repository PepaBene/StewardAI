import type { SankeyLinkInput, SankeyNodeInput } from "@/components/charts";
import type { components } from "@/lib/api/schema";

/*
 * Customer, supplier and revenue dependency (plan P5): types and pure logic for the page. Same rules
 * as the backend's `disclosure/dependencies.py`: only confirmed claims carry numbers, an anonymous
 * counterparty is never given a name, shares of different bases are never added, what no claim
 * accounts for is shown as an unknown remainder, and a scenario runs only over numeric confirmed edges.
 */

type S = components["schemas"];
export type DependencyView = S["DependencyViewOut"];
export type Claim = S["DependencyClaimOut"];
export type Concentration = S["ConcentrationOut"];
export type Scenario = S["ScenarioOut"];

export const STATE_LABEL: Record<string, string> = {
  confirmed: "Confirmed",
  suggested: "Suggested, not reviewed",
  disputed: "Disputed",
  rejected: "Rejected",
};

export const SHAPE_LABEL: Record<string, string> = {
  named: "Named",
  anonymous: "Not named in the filing",
  group: "Group of customers",
  segment_only: "Segment or region only",
};

export const DENOMINATOR_LABEL: Record<string, string> = {
  total_revenue: "of total revenue",
  segment_revenue: "of segment revenue",
  cost_of_sales: "of cost of sales",
  purchases: "of purchases",
  receivables: "of receivables",
  other: "of another total",
};

export const fraction = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export const formatShare = (v: string | number | null | undefined, digits = 0): string => {
  const n = typeof v === "number" ? v : fraction(v);
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
};

export const periodLabel = (p: string): string => (/^\d{4}-\d{2}-\d{2}$/.test(p) ? `Year to ${p}` : p === "undated" ? "No period given" : p);

// ----------------------------------------------------------------------------- concentration
export type BarPart = { key: string; label: string; share: number; kind: "single" | "remainder" };

/** Parts of one concentration bar, in draw order. The remainder is an open gap, never a customer. */
export function concentrationParts(c: Concentration): BarPart[] {
  const parts: BarPart[] = c.entries.map((e) => ({ key: `e${e.id}`, label: e.label, share: fraction(e.share) ?? 0, kind: "single" }));
  const used = parts.reduce((a, p) => a + p.share, 0);
  const rest = Math.max(0, 1 - Math.max(used, fraction(c.reported_floor) ?? 0));
  if (rest > 0) parts.push({ key: "rest", label: "Not accounted for by any confirmed claim", share: rest, kind: "remainder" });
  return parts;
}

/** Client copy of `concentration`, used for the sample: one result per (basis, period), confirmed only. */
export function concentrationOf(claims: readonly Claim[]): Concentration[] {
  const keyOf = (c: Claim) => `${c.denominator}|${c.period_end ?? "undated"}`;
  const usable = claims.filter((c) => c.relationship === "customer" && c.share != null && c.denominator != null && c.shape !== "segment_only" && c.problems.length === 0);
  const keys = [...new Set(usable.map(keyOf))];
  const out: Concentration[] = [];
  for (const k of keys) {
    const rows = usable.filter((c) => keyOf(c) === k);
    const [den, period] = k.split("|");
    const confirmed = rows.filter((c) => c.status === "confirmed");
    const singles = confirmed.filter((c) => c.shape !== "group").sort((a, b) => (fraction(b.share) ?? 0) - (fraction(a.share) ?? 0) || (a.counterparty ?? "").localeCompare(b.counterparty ?? ""));
    const groups = confirmed.filter((c) => c.shape === "group");
    const lower = singles.reduce((a, c) => a + (fraction(c.share) ?? 0), 0);
    const floor = Math.max(lower, ...groups.map((g) => fraction(g.share) ?? 0));
    out.push({
      relationship: "customer",
      denominator: den,
      period,
      entries: singles.map((c) => ({ id: c.id, label: c.counterparty ?? "Customer (not named)", shape: c.shape, share: String(c.share), evidence_id: c.evidence_id, known_at: c.known_at })),
      groups: groups.map((g) => ({ id: g.id, label: g.counterparty ?? "Group of customers", count: g.counterparty_count ?? 0, share: String(g.share) })),
      lower_bound: String(lower),
      reported_floor: String(floor),
      unknown_remainder: String(Math.max(0, 1 - floor)),
      largest: singles[0]?.counterparty ?? (singles[0] ? "Customer (not named)" : null),
      exceeds_whole: lower > 1,
      suggested_not_counted: rows.length - confirmed.length,
      notes: groups.length ? ["A group figure may already include customers listed separately, so it is not added to them; it only raises the floor."] : [],
    });
  }
  return out.sort((a, b) => b.period.localeCompare(a.period));
}

// ---------------------------------------------------------------------------------- the graph
export type GraphNode = { id: string; label: string; x: number; y: number; kind: "issuer" | "customer" | "supplier" | "award" };
export type GraphEdge = { id: number; from: string; to: string; share: number | null; status: string; shape: string; stale: boolean; label: string };

const COL = { supplier: 0.14, issuer: 0.5, customer: 0.86 } as const;

/**
 * Suppliers on the left, the company in the middle, customers on the right; every arrow points the
 * way the money flows (supplier → company is a purchase, company → customer is a sale). Anonymous
 * claims keep their own node each: two unnamed customers are never merged.
 */
export function graphLayout(ticker: string, claims: readonly Claim[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodes: GraphNode[] = [{ id: "issuer", label: ticker, x: COL.issuer, y: 0.5, kind: "issuer" }];
  const edges: GraphEdge[] = [];
  const sides = [
    claims.filter((c) => c.relationship === "supplier"),
    claims.filter((c) => c.relationship !== "supplier"),
  ];
  for (const side of sides) {
    const group = side.filter((c) => c.status !== "rejected");
    group.forEach((c, i) => {
      const kind: GraphNode["kind"] = c.relationship === "supplier" ? "supplier" : c.relationship === "procurement_award" ? "award" : "customer";
      const id = `${kind}:${c.id}`;
      nodes.push({ id, label: c.counterparty ?? (kind === "supplier" ? "Supplier (not named)" : kind === "award" ? "Awarding body" : "Customer (not named)"), x: kind === "supplier" ? COL.supplier : COL.customer, y: (i + 1) / (group.length + 1), kind });
      edges.push({
        id: c.id,
        from: kind === "supplier" ? id : "issuer",
        to: kind === "supplier" ? "issuer" : id,
        share: kind === "award" ? null : fraction(c.share),
        status: c.status,
        shape: c.shape,
        stale: c.stale,
        label: kind === "award" ? "contract award" : c.share ? `${formatShare(c.share)} ${DENOMINATOR_LABEL[c.denominator ?? "other"]}` : "no share reported",
      });
    });
  }
  return { nodes, edges };
}

// ------------------------------------------------------------------------------- the portfolio
export type PortfolioHolding = { ticker: string; weight: number; claims: readonly Claim[] };

/**
 * Where a portfolio's revenue exposure sits: each holding flows to the customers it has confirmed
 * numeric shares for, sized by portfolio weight × share of total revenue. What is not disclosed flows
 * to its own node. Suggested, disputed and rejected claims are not drawn at all.
 */
export function portfolioSankey(holdings: readonly PortfolioHolding[]): { nodes: SankeyNodeInput[]; links: SankeyLinkInput[]; rows: { holding: string; counterparty: string; value: number }[] } {
  const nodes: SankeyNodeInput[] = [];
  const links: SankeyLinkInput[] = [];
  const rows: { holding: string; counterparty: string; value: number }[] = [];
  let hasRest = false;
  for (const h of holdings) {
    const edges = h.claims.filter((c) => c.relationship === "customer" && c.status === "confirmed" && c.denominator === "total_revenue" && (c.shape === "named" || c.shape === "anonymous") && fraction(c.share) != null);
    if (edges.length === 0) continue;
    nodes.push({ id: `h:${h.ticker}`, label: `${h.ticker} · ${(h.weight * 100).toFixed(0)}%`, column: 0 });
    let used = 0;
    for (const c of edges) {
      const value = h.weight * (fraction(c.share) as number);
      used += value;
      const id = c.shape === "named" ? `c:${c.counterparty}` : `c:${h.ticker}:${c.id}`;
      if (!nodes.some((n) => n.id === id)) nodes.push({ id, label: c.shape === "named" ? (c.counterparty as string) : `${h.ticker}'s unnamed customer`, column: 1 });
      links.push({ source: `h:${h.ticker}`, target: id, value, emphasis: true });
      rows.push({ holding: h.ticker, counterparty: c.counterparty ?? "Not named", value });
    }
    const rest = Math.max(0, h.weight - used);
    if (rest > 0) {
      hasRest = true;
      links.push({ source: `h:${h.ticker}`, target: "c:rest", value: rest });
    }
  }
  if (hasRest) nodes.push({ id: "c:rest", label: "Not disclosed", column: 1 });
  return { nodes, links, rows };
}

/** The portfolio read from the backend: weights come from the holdings, shares from confirmed claims. */
export type PortfolioDependencies = S["PortfolioDependenciesOut"];
export type OwnerWeight = S["OwnerWeightOut"];
export type SideConflict = S["SideConflictOut"];

/**
 * Holding to customer flows from `/portfolio/dependencies`. Each flow is weight times reported
 * share. A holding with no confirmed numeric claim has no flow and is listed as unknown elsewhere,
 * never drawn as a zero.
 */
export function liveSankey(p: PortfolioDependencies): { nodes: SankeyNodeInput[]; links: SankeyLinkInput[] } {
  const nodes: SankeyNodeInput[] = [];
  const links: SankeyLinkInput[] = [];
  for (const f of p.flows) {
    const value = Number(f.exposure);
    if (!Number.isFinite(value) || value <= 0) continue;
    const from = `h:${f.holding}`;
    const to = `c:${f.counterparty}`;
    if (!nodes.some((n) => n.id === from)) nodes.push({ id: from, label: f.holding, column: 0 });
    if (!nodes.some((n) => n.id === to)) nodes.push({ id: to, label: f.counterparty, column: 1 });
    links.push({ source: from, target: to, value, emphasis: true });
  }
  return { nodes, links };
}

// --------------------------------------------------------------------------------- the scenario
export type SEdge = { id: number; supplier: string; customer: string; share: number | null; status: string; knownMs: number };

export type SImpact = { entity: string; hop: number; lower: number; upper: number; coverage: number; via: number[] };

/** Client copy of `shock_scenario`, used for the sample. The live page uses the backend's answer. */
export function shockScenario(edges: readonly SEdge[], origin: string, shock: number, cutoffMs: number, maxHops: 1 | 2 = 2): { impacts: SImpact[]; nonQuantified: SEdge[] } {
  const live = edges.filter((e) => e.knownMs <= cutoffMs && e.status === "confirmed");
  const numeric = live.filter((e) => e.share != null);
  const cover = (who: string) => Math.min(1, numeric.filter((e) => e.supplier === who).reduce((a, e) => a + (e.share as number), 0));
  const impacts: SImpact[] = [];
  let frontier: { who: string; lower: number; via: number[] }[] = [{ who: origin, lower: shock, via: [] }];
  const seen = new Set([origin]);
  for (let hop = 1; hop <= maxHops; hop++) {
    const next: typeof frontier = [];
    for (const f of frontier) {
      for (const e of numeric.filter((x) => x.customer === f.who).sort((a, b) => a.id - b.id)) {
        if (seen.has(e.supplier)) continue;
        const lower = (e.share as number) * f.lower;
        const coverage = cover(e.supplier);
        impacts.push({ entity: e.supplier, hop, lower, upper: Math.min(1, lower + (1 - coverage) * shock), coverage, via: [...f.via, e.id] });
        next.push({ who: e.supplier, lower, via: [...f.via, e.id] });
      }
    }
    next.forEach((n) => seen.add(n.who));
    frontier = next;
  }
  const reached = new Set([origin, ...impacts.map((i) => i.entity)]);
  return { impacts, nonQuantified: live.filter((e) => e.share == null && (reached.has(e.customer) || reached.has(e.supplier))) };
}
