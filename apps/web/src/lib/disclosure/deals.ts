import type { WaterfallStep } from "@/components/charts";
import type { components } from "@/lib/api/schema";
import { formatCompact } from "@/lib/charts/format";
import { linearScale } from "@/lib/charts/scale";

/*
 * Public deals, financing and corporate actions (plan P6): types and pure logic for the page. Same
 * rules as the backend's `disclosure/deals.py` (the backend is the reference; this copy only powers
 * the labelled sample): a state change needs evidence and a known-at date, an illegal change is
 * rejected with its reason, a view uses only what was known by the date, an amendment adds a term
 * version, a term that is not stated is an open unknown and never zero, and an impact is null with a
 * reason when an input is missing. The sample covers a subset of the backend's impacts (premium,
 * dilution, debt against enterprise value, buyback use, synergies and your scenario).
 */

type S = components["schemas"];
export type HoldingDeals = S["HoldingDealsOut"];
export type Deal = S["DealOut"];
export type Impact = S["DealImpactOut"];
export type DealTransition = S["DealTransitionOut"];
export type TermVersion = S["DealTermVersionOut"];
export type Bridge = S["DealBridgeOut"];
export type BridgeSegment = S["DealBridgeSegmentOut"];
export type PortfolioDeals = S["PortfolioDealsOut"];
export type DealClosing = S["DealClosingOut"];

export const FORMULA_VERSION = "deal-impacts-1";
export const STALE_UNAFFECTED_DAYS = 10;
const DAY_MS = 86_400_000;

export const STATE_LABEL: Record<string, string> = {
  rumored: "Rumored",
  announced: "Announced",
  definitive: "Definitive agreement",
  approved: "Approved",
  closed: "Closed",
  terminated: "Terminated",
  contested: "Contested",
};
export const TERMINAL = new Set(["closed", "terminated"]);
export const ACTIVE = new Set(["rumored", "announced", "definitive", "approved", "contested"]);

export const TYPE_LABEL: Record<string, string> = {
  acquisition: "Acquisition",
  merger: "Merger",
  divestiture: "Divestiture or asset sale",
  ipo: "IPO",
  follow_on: "Follow-on offering",
  debt_issuance: "Debt issuance or refinancing",
  spin_off: "Spin-off",
  joint_venture: "Joint venture",
  strategic_investment: "Strategic investment",
  buyback: "Buyback",
  tender_offer: "Tender offer",
  material_agreement: "Material commercial agreement",
};

export const ORIGIN_LABEL: Record<string, string> = {
  announced: "Announced terms",
  management_projection: "Management projection",
  owner_scenario: "Your scenario",
};

export const CONFIDENCE_LABEL: Record<string, string> = {
  high: "Well supported",
  medium: "Some required terms missing",
  low: "Weakly supported (rumor, unresolved state or rejected change)",
};

export const TERM_NAMES = [
  "price_per_share", "cash_amount", "stock_amount", "debt_amount", "earnout_amount", "cash_pct", "stock_pct",
  "exchange_ratio", "stake_pct", "financing_amount", "refinanced_amount", "termination_fee",
  "expected_synergies", "authorized_amount", "executed_amount", "unaffected_price",
] as const;
export const TERM_LABEL: Record<string, string> = {
  price_per_share: "Price per share",
  cash_amount: "Cash",
  stock_amount: "Stock",
  debt_amount: "Debt",
  earnout_amount: "Earnout",
  cash_pct: "Cash share",
  stock_pct: "Stock share",
  exchange_ratio: "Exchange ratio",
  stake_pct: "Stake",
  financing_amount: "Financing",
  refinanced_amount: "Debt refinanced",
  termination_fee: "Termination fee",
  expected_synergies: "Expected synergies",
  authorized_amount: "Buyback authorization",
  executed_amount: "Bought back so far",
  unaffected_price: "Unaffected price",
  closing_conditions: "Closing conditions",
  currency: "Currency",
  consideration: "Consideration",
  unaffected_date: "Unaffected price date",
};
const PCT_TERMS = new Set(["cash_pct", "stock_pct", "stake_pct"]);
const PRICE_TERMS = new Set(["price_per_share", "unaffected_price"]);

// -------------------------------------------------------------------------------- formatting
export const num = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** One term in plain units: a price to two decimals, a share as a percent, an amount compact. */
export function formatTerm(name: string, value: string | null | undefined, currency: string | null | undefined): string {
  const n = num(value);
  if (n == null) return "not stated";
  if (PCT_TERMS.has(name)) return `${(n * 100).toFixed(n * 100 % 1 === 0 ? 0 : 1)}%`;
  if (name === "exchange_ratio") return `${Number(n.toFixed(4))}`;
  if (PRICE_TERMS.has(name)) return `${currency ?? ""} ${n.toFixed(2)}`.trim();
  return `${currency ?? ""} ${formatCompact(n)}`.trim();
}

export function describeChange(c: { field: string; label: string; old: string | null; new: string | null }, currency: string | null): string {
  if (c.field === "closing_conditions") return `${c.label} ${c.old == null ? "added" : c.new == null ? "removed" : "changed"}`;
  const f = (v: string | null) => (v == null ? "not stated" : c.field === "currency" ? v : formatTerm(c.field, v, currency));
  return `${c.label.toLowerCase()} ${f(c.old)} → ${f(c.new)}`;
}

export function versionSummary(v: TermVersion): string {
  if (v.changes.length === 0) return "no change in terms";
  const first = describeChange(v.changes[0], v.currency);
  return v.changes.length > 1 ? `${first} (+${v.changes.length - 1} more)` : first;
}

export const money = (v: string | number | null | undefined, currency: string | null | undefined): string => {
  const n = typeof v === "number" ? v : num(v);
  return n == null ? "not stated" : `${currency ?? ""} ${formatCompact(n)}`.trim();
};

export const percent = (v: string | null | undefined, digits = 1): string => {
  const n = num(v);
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
};

export function impactValue(i: Impact): string {
  const n = num(i.value);
  if (n == null) return "Not computable";
  if (i.unit === "fraction") return percent(i.value);
  if (i.unit === "shares") return formatCompact(n);
  if (i.unit === "currency") return money(n, i.currency);
  return `${Number(n.toFixed(4))}`;
}

export const FLAG_LABEL: Record<string, string> = {
  stale_unaffected_price: "Unaffected price is older than 10 days before the announcement",
  gross_of_refinancing: "Gross of any debt repaid: the repayment is not stated",
  executed_exceeds_authorization: "Purchases stated above the authorization: check the figures",
};

// ------------------------------------------------------------------------------ state machine
const FULL = ["rumored", "announced", "definitive", "approved", "closed", "terminated", "contested"];
const NO_CONTEST = FULL.filter((s) => s !== "contested");
const PLAIN = ["rumored", "announced", "definitive", "closed", "terminated"];
const ISSUE = ["announced", "definitive", "closed", "terminated"];
export const TYPE_STATES: Record<string, string[]> = {
  acquisition: FULL, merger: FULL, divestiture: FULL, ipo: PLAIN, follow_on: ISSUE, debt_issuance: ISSUE,
  spin_off: NO_CONTEST, joint_venture: FULL, strategic_investment: FULL, buyback: ISSUE, tender_offer: FULL,
  material_agreement: ISSUE,
};
const START = new Set(["rumored", "announced", "definitive"]);
const NEXT: Record<string, string[]> = {
  rumored: ["announced", "definitive", "terminated"],
  announced: ["definitive", "approved", "closed", "terminated", "contested"],
  definitive: ["approved", "closed", "terminated", "contested"],
  approved: ["closed", "terminated", "contested"],
  contested: ["announced", "definitive", "approved", "closed", "terminated"],
  closed: [],
  terminated: [],
};

/** Why a move is illegal for this type, or null. */
export function transitionProblem(dealType: string, from: string | null, to: string): string | null {
  const states = TYPE_STATES[dealType];
  if (!states) return `unknown deal type ${dealType}`;
  if (!(to in NEXT)) return `unknown state ${to}`;
  if (!states.includes(to)) return `a ${(TYPE_LABEL[dealType] ?? dealType).toLowerCase()} cannot be ${to}`;
  if (from == null) return START.has(to) ? null : `a deal cannot first be seen as ${to}; it needs an earlier state`;
  if (from === to) return `already ${to}; an amendment adds a term version, not a state`;
  if (!NEXT[from].includes(to)) return `${from} cannot move to ${to}${TERMINAL.has(from) ? " (a closed or terminated deal is final)" : ""}`;
  return null;
}

export type StateResolution = { state: string | null; applied: DealTransition[]; rejected: { id: number; state: string; reason: string }[] };

/** The state from the transitions KNOWN by `cutoff`, applied in the order they became known. */
export function resolveState(dealType: string, transitions: readonly DealTransition[], cutoff: number): StateResolution {
  const known = transitions
    .filter((t) => Date.parse(t.known_at) <= cutoff)
    .sort((a, b) => Date.parse(a.known_at) - Date.parse(b.known_at) || Date.parse(a.effective_at) - Date.parse(b.effective_at) || a.id - b.id);
  const applied: DealTransition[] = [];
  const rejected: StateResolution["rejected"] = [];
  let current: string | null = null;
  for (const t of known) {
    let problem: string | null = null;
    if (t.evidence_id == null) problem = "evidence is mandatory for a state change";
    else if (t.state === "rumored" && !(t.rumor_source ?? "").trim()) problem = "a rumor must name its source and is shown labelled as a rumor";
    else problem = transitionProblem(dealType, current, t.state);
    const last = applied.at(-1);
    if (!problem && last && Date.parse(t.effective_at) < Date.parse(last.effective_at)) problem = "effective date is before the previous state's";
    if (problem) {
      rejected.push({ id: t.id, state: t.state, reason: problem });
      continue;
    }
    applied.push(t);
    current = t.state;
  }
  return { state: current, applied, rejected };
}

export function confidence(state: string | null, rejected: number, missingRequired: number): "high" | "medium" | "low" {
  if (state == null || state === "rumored" || rejected > 0) return "low";
  return missingRequired > 0 ? "medium" : "high";
}

// ------------------------------------------------------------------------------------- impacts
const impact = (p: Partial<Impact> & Pick<Impact, "kind" | "label" | "origin" | "unit" | "formula">, ids: number[], currency: string | null): Impact => ({
  value: null, currency, reason: null, flags: [], inputs: {}, detail: {}, formula_version: FORMULA_VERSION, term_version_ids: ids, ...p,
});

export function premiumImpact(tv: TermVersion | undefined, announcedOn: string | null, offerOverride?: number | null): Impact {
  const scenario = offerOverride != null;
  const base = { kind: "premium", label: scenario ? "Premium in your scenario" : "Announced premium", origin: (scenario ? "owner_scenario" : "announced") as Impact["origin"], unit: "fraction" as const, formula: "offer / unaffected - 1" };
  const ids = tv ? [tv.id] : [];
  const cur = tv?.currency ?? null;
  const value = (n: string) => tv?.terms.find((t) => t.name === n)?.value ?? null;
  const offer = scenario ? offerOverride : num(value("price_per_share"));
  const un = num(value("unaffected_price"));
  const inputs: Record<string, string> = {};
  if (offer != null) inputs.offer_price = String(offer);
  if (un != null) inputs.unaffected_price = String(un);
  if (tv?.unaffected_date) inputs.unaffected_date = tv.unaffected_date;
  if (announcedOn) inputs.announced_on = announcedOn;
  if (cur) inputs.currency = cur;
  const nul = (reason: string) => impact({ ...base, inputs, reason }, ids, cur);
  if (!tv) return nul("no terms are known at this date");
  if (offer == null) return nul("no per-share price is stated; a headline total is never divided into a price");
  if (un == null) return nul("no unaffected price is stated");
  if (!tv.unaffected_date) return nul("the unaffected price has no date, so it cannot be called unaffected");
  if (!announcedOn) return nul("the announcement date is not known");
  if (tv.unaffected_date >= announcedOn) return nul(`the unaffected date ${tv.unaffected_date} is on or after the announcement ${announcedOn}, so that price already reflects the deal`);
  if (!cur) return nul("the currency is not stated");
  if (un <= 0 || offer <= 0) return nul("a price must be positive");
  const age = (Date.parse(announcedOn) - Date.parse(tv.unaffected_date)) / DAY_MS;
  return impact({ ...base, inputs, value: String(offer / un - 1), flags: age > STALE_UNAFFECTED_DAYS ? ["stale_unaffected_price"] : [] }, ids, cur);
}

export type DealContext = { acquirerShares?: number; targetShares?: number; enterpriseValueBefore?: number; netDebtBefore?: number };

export function dilutionImpact(tv: TermVersion | undefined, ctx: DealContext): Impact {
  const base = { kind: "dilution", label: "Dilution from new shares", origin: "announced" as const, unit: "fraction" as const, formula: "ratio * target / (acquirer + ratio * target)" };
  const ratio = num(tv?.terms.find((t) => t.name === "exchange_ratio")?.value);
  const ids = tv ? [tv.id] : [];
  if (ratio == null) return impact({ ...base, reason: "no exchange ratio is stated (an all-cash price issues no shares we can count)" }, ids, tv?.currency ?? null);
  if (ctx.acquirerShares == null || ctx.targetShares == null) return impact({ ...base, inputs: { exchange_ratio: String(ratio) }, reason: "the share counts are not known" }, ids, tv?.currency ?? null);
  const added = ratio * ctx.targetShares;
  return impact(
    {
      ...base,
      value: String(added / (ctx.acquirerShares + added)),
      inputs: { exchange_ratio: String(ratio), acquirer_shares: String(ctx.acquirerShares), target_shares: String(ctx.targetShares) },
      detail: { new_shares: String(added), pro_forma_shares: String(ctx.acquirerShares + added) },
    },
    ids,
    tv?.currency ?? null,
  );
}

export function debtImpact(dealType: string, tv: TermVersion | undefined, ctx: DealContext): Impact {
  const field = dealType === "debt_issuance" ? "financing_amount" : "debt_amount";
  const base = { kind: "debt_ev", label: "New debt against enterprise value", origin: "announced" as const, unit: "fraction" as const, formula: "(new debt - refinanced) / enterprise value before" };
  const ids = tv ? [tv.id] : [];
  const cur = tv?.currency ?? null;
  const gross = num(tv?.terms.find((t) => t.name === field)?.value);
  const repaid = num(tv?.terms.find((t) => t.name === "refinanced_amount")?.value);
  if (gross == null) return impact({ ...base, reason: "no debt amount is stated" }, ids, cur);
  if (ctx.enterpriseValueBefore == null || ctx.enterpriseValueBefore <= 0) return impact({ ...base, inputs: { [field]: String(gross) }, reason: "the enterprise value before the deal is not known" }, ids, cur);
  const net = gross - (repaid ?? 0);
  const inputs: Record<string, string> = { [field]: String(gross), enterprise_value_before: String(ctx.enterpriseValueBefore) };
  if (repaid != null) inputs.refinanced_amount = String(repaid);
  return impact({ ...base, value: String(net / ctx.enterpriseValueBefore), inputs, detail: { net_new_debt: String(net) }, flags: repaid == null && dealType === "debt_issuance" ? ["gross_of_refinancing"] : [] }, ids, cur);
}

export function buybackImpact(tv: TermVersion | undefined): Impact {
  const base = { kind: "buyback", label: "Share of authorization used", origin: "announced" as const, unit: "fraction" as const, formula: "executed / authorized" };
  const ids = tv ? [tv.id] : [];
  const auth = num(tv?.terms.find((t) => t.name === "authorized_amount")?.value);
  const done = num(tv?.terms.find((t) => t.name === "executed_amount")?.value);
  if (auth == null) return impact({ ...base, reason: "no authorization amount is stated" }, ids, tv?.currency ?? null);
  if (done == null) return impact({ ...base, inputs: { authorized_amount: String(auth) }, reason: "no purchases are stated; an authorization is not a purchase" }, ids, tv?.currency ?? null);
  return impact({ ...base, value: String(done / auth), inputs: { authorized_amount: String(auth), executed_amount: String(done) }, flags: done > auth ? ["executed_exceeds_authorization"] : [] }, ids, tv?.currency ?? null);
}

export function synergyImpact(versions: readonly TermVersion[]): Impact {
  const base = { kind: "synergies", label: "Management's expected synergies", origin: "management_projection" as const, unit: "currency" as const, formula: "as stated" };
  const having = versions.filter((v) => v.terms.some((t) => t.name === "expected_synergies" && t.state === "stated")).sort((a, b) => b.version_no - a.version_no || b.id - a.id)[0];
  if (!having) return impact({ ...base, reason: "no synergy figure is stated" }, [], null);
  const v = having.terms.find((t) => t.name === "expected_synergies")!.value;
  return impact({ ...base, value: v, inputs: { expected_synergies: v ?? "" } }, [having.id], having.currency ?? null);
}

// ------------------------------------------------------------------------------------- bridge
export type BridgePart = { kind: string; label: string; state: "stated" | "not_stated"; amount: number | null; x: number; w: number };
const BRIDGE_LABEL: Record<string, string> = { cash: "Cash", stock: "Stock", debt: "Debt", earnout: "Earnout" };

/** Segment geometry: stated parts share the width in proportion; a part not stated is an open
 * segment of fixed width, never a zero-width one. */
export function bridgeParts(b: Bridge, width = 720): BridgePart[] {
  const open = b.segments.filter((s) => s.state === "not_stated").length;
  const total = b.segments.reduce((a, s) => a + (num(s.amount) ?? 0), 0);
  const openW = open > 0 ? (total > 0 ? Math.min(0.2, 0.2 * open) / open : 1 / b.segments.length) * width : 0;
  const statedW = width - openW * open;
  let x = 0;
  return b.segments.map((s) => {
    const amount = num(s.amount);
    const w = s.state === "not_stated" || amount == null ? openW : total > 0 ? (amount / total) * statedW : statedW / Math.max(1, b.segments.length - open);
    const part = { kind: s.kind, label: BRIDGE_LABEL[s.kind] ?? s.kind, state: s.state, amount, x, w };
    x += w;
    return part;
  });
}

// ---------------------------------------------------------------------------------- waterfalls
export type ImpactWaterfall = { id: string; title: string; origin: Impact["origin"]; steps: WaterfallStep[]; format: (v: number) => string; note: string };

/** Waterfalls only where the impact has its inputs; the rest are listed with the reason they are not drawn. */
export function impactWaterfalls(deal: Deal): { shown: ImpactWaterfall[]; notDrawn: { title: string; reason: string }[] } {
  const shown: ImpactWaterfall[] = [];
  const notDrawn: { title: string; reason: string }[] = [];
  for (const i of deal.impacts) {
    if (!["premium", "dilution", "debt_ev"].includes(i.kind)) continue;
    const scenario = i.origin === "owner_scenario";
    const id = `${i.kind}-${i.origin}`;
    const dashed = scenario;
    const cur = i.currency ?? "";
    if (i.value == null) {
      notDrawn.push({ title: i.label, reason: i.reason ?? "an input is missing" });
      continue;
    }
    if (i.kind === "premium") {
      const un = Number(i.inputs.unaffected_price);
      const offer = Number(i.inputs.offer_price);
      shown.push({ id, title: i.label, origin: i.origin, format: (v) => v.toFixed(2), note: `Price per share${cur ? `, ${cur}` : ""}, unaffected price dated ${i.inputs.unaffected_date ?? "?"}`,
        steps: [{ id: "a", label: "Unaffected", value: un, kind: "total", derived: dashed }, { id: "b", label: "Premium", value: offer - un, kind: "delta", derived: dashed }, { id: "c", label: scenario ? "Your offer" : "Offer", value: offer, kind: "total", derived: dashed }] });
    } else if (i.kind === "dilution") {
      const acq = Number(i.inputs.acquirer_shares);
      const added = Number(i.detail.new_shares);
      shown.push({ id, title: i.label, origin: i.origin, format: formatCompact, note: "Shares outstanding after the deal",
        steps: [{ id: "a", label: "Acquirer today", value: acq, kind: "total" }, { id: "b", label: "New shares", value: added, kind: "delta" }, { id: "c", label: "Combined", value: acq + added, kind: "total" }] });
    } else {
      const ev = Number(i.inputs.enterprise_value_before);
      const nd = Number(i.detail.net_new_debt);
      shown.push({ id, title: i.label, origin: i.origin, format: (v) => `${cur} ${formatCompact(v)}`.trim(), note: "Enterprise value if the new net debt adds to it and nothing else changes",
        steps: [{ id: "a", label: "Before", value: ev, kind: "total" }, { id: "b", label: "New net debt", value: nd, kind: "delta" }, { id: "c", label: "After", value: ev + nd, kind: "total" }] });
    }
  }
  return { shown, notDrawn };
}

// ----------------------------------------------------------------------------------- timeline
export type RailMark = { id: number; state: string; x: number; xKnown: number; effective: string; known: string; terminal: boolean; late: boolean };
export type VersionMark = { id: number; x: number; versionNo: number; origin: TermVersion["origin"]; summary: string; known: string };
export type Timeline = { marks: RailMark[]; versions: VersionMark[]; ticks: { x: number; label: string }[]; domain: [number, number] };

/** Positions for the lifecycle timeline. The state rail is placed by when each change TOOK EFFECT; the
 * known-at tick sits at when it BECAME KNOWN; the version track is placed by known-at. */
export function timelineLayout(deal: Pick<Deal, "transitions" | "term_versions">, width: number, pad = 64): Timeline {
  const stamps = [
    ...deal.transitions.flatMap((t) => [Date.parse(t.effective_at), Date.parse(t.known_at)]),
    ...deal.term_versions.map((v) => Date.parse(v.known_at)),
  ].filter(Number.isFinite);
  if (stamps.length === 0) return { marks: [], versions: [], ticks: [], domain: [0, 0] };
  let lo = Math.min(...stamps);
  let hi = Math.max(...stamps);
  if (hi - lo < 14 * DAY_MS) {
    lo -= 7 * DAY_MS;
    hi += 7 * DAY_MS;
  }
  const x = linearScale([lo, hi], [pad, width - pad]);
  const marks = deal.transitions.map((t) => ({
    id: t.id, state: t.state, x: x(Date.parse(t.effective_at)), xKnown: x(Date.parse(t.known_at)),
    effective: t.effective_at, known: t.known_at, terminal: TERMINAL.has(t.state) || t.state === "contested",
    late: Date.parse(t.known_at) - Date.parse(t.effective_at) > 0,
  }));
  const versions = deal.term_versions.map((v) => ({ id: v.id, x: x(Date.parse(v.known_at)), versionNo: v.version_no, origin: v.origin, summary: versionSummary(v), known: v.known_at }));
  const ticks: { x: number; label: string }[] = [];
  const start = new Date(lo);
  const spanMonths = (hi - lo) / (30 * DAY_MS);
  const step = spanMonths > 18 ? 6 : spanMonths > 8 ? 3 : spanMonths > 3 ? 1 : 0;
  if (step > 0) {
    const d = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
    while (d.getTime() < hi) {
      ticks.push({ x: x(d.getTime()), label: d.toISOString().slice(0, 7) });
      d.setUTCMonth(d.getUTCMonth() + step);
    }
  } else {
    for (let t = Math.ceil(lo / (7 * DAY_MS)) * 7 * DAY_MS; t < hi; t += 7 * DAY_MS) ticks.push({ x: x(t), label: new Date(t).toISOString().slice(5, 10) });
  }
  return { marks, versions, ticks, domain: [lo, hi] };
}

// ----------------------------------------------------------------------- filters, portfolio
/** `minSize` is a number in the deal's own currency; a deal with no stated size never passes a size filter (missing is not zero). */
export type DealFilter = { type: string; state: string; currency?: string; minSize?: number | null };

export const filterDeals = (deals: readonly Deal[], f: DealFilter): Deal[] =>
  deals.filter((d) => {
    if (f.type !== "" && d.deal_type !== f.type) return false;
    if (f.state !== "" && (f.state === "unknown" ? d.state != null : d.state !== f.state)) return false;
    if (f.currency && d.currency !== f.currency) return false;
    if (f.minSize != null) {
      const size = d.size == null ? null : Number(d.size);
      if (size == null || !Number.isFinite(size) || size < f.minSize) return false;
    }
    return true;
  });

export type DealRef = { ticker: string; dealId: number; title: string; dealType: string; state: string | null };

/** Portfolio weight in holdings with an ACTIVE deal known at the date. A holding with none is listed
 * "none known", which is not proof that none exists. */
export function portfolioExposure(weights: Record<string, number>, refs: readonly DealRef[], asOf: string): PortfolioDeals {
  const denominator = Object.values(weights).reduce((a, b) => a + b, 0);
  const rows = refs.filter((r) => r.ticker in weights && r.state != null && ACTIVE.has(r.state));
  const covered = new Set(rows.map((r) => r.ticker));
  const add = (m: Record<string, string>, k: string, w: number) => {
    m[k] = String((Number(m[k] ?? 0)) + w);
  };
  const byState: Record<string, string> = {};
  const byType: Record<string, string> = {};
  for (const r of rows) {
    add(byState, r.state!, weights[r.ticker]);
    add(byType, r.dealType, weights[r.ticker]);
  }
  const notes: string[] = [];
  if (rows.length > covered.size) notes.push("A holding with two active deals appears in both, so the rows can add up to more than the covered weight.");
  return {
    as_of: asOf,
    holdings: Object.keys(weights).length,
    denominator: String(denominator),
    covered_weight: String([...covered].reduce((a, t) => a + weights[t], 0)),
    none_known: Object.keys(weights).filter((t) => !covered.has(t) && !refs.some((r) => r.ticker === t)).sort(),
    unknown_state: [...new Set(refs.filter((r) => r.ticker in weights && r.state == null).map((r) => r.ticker))].sort(),
    by_state: byState,
    by_type: byType,
    rows: rows.map((r) => ({ ticker: r.ticker, deal_id: r.dealId, title: r.title, deal_type: r.dealType as Deal["deal_type"], state: r.state as Deal["state"], weight: String(weights[r.ticker]) })),
    notes,
  };
}
