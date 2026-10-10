import {
  buybackImpact,
  confidence,
  dilutionImpact,
  portfolioExposure,
  premiumImpact,
  resolveState,
  synergyImpact,
  debtImpact,
  TERM_LABEL,
  TERM_NAMES,
  TYPE_LABEL,
  type Bridge,
  type Deal,
  type DealClosing,
  type DealContext,
  type DealRef,
  type DealTransition,
  type HoldingDeals,
  type PortfolioDeals,
  type TermVersion,
} from "@/lib/disclosure/deals";

/*
 * Invented deals for the labelled sample mode. "Acme Corp" and every counterparty are made up and
 * no figure here is real. The data shows the hard cases: a contested bid, an amendment that raised
 * the price, cash plus stock, a withdrawn approach, a buyback authorization against purchases, an
 * asset sale by the same party on the same day, and closing dates that are not all known yet.
 * Everything is stored with its known-at date and `buildDeal` shows only what was known by a cutoff.
 */

export const SAMPLE_DEAL_TICKER = "ACME";
export const SAMPLE_DEAL_DAY = "2026-09-30";
export const SAMPLE_DEAL_NAMES: Record<string, string> = { ACME: "Acme Corp", DUNE: "Dune Holdings", FJORD: "Fjord Energy", GLEN: "Glen Foods" };

const at = (day: string) => `${day}T12:00:00Z`;
const URL = (slug: string) => `https://example.invalid/sample/${slug}`;

type RawTransition = { id: number; state: string; eff: string; known: string; quote: string; slug: string; rumor?: string };
type RawVersion = { id: number; no: number; origin: "announced" | "management_projection"; known: string; currency: string | null; values: Record<string, string>; conditions?: string; unaffectedDate?: string; unaffectedSource?: string; quote: string; slug: string };
type RawDeal = {
  id: number; key: string; type: Deal["deal_type"]; title: string; announced: string;
  participants: { role: string; name: string; ticker?: string }[];
  transitions: RawTransition[]; versions: RawVersion[];
  documents: { versionId: number; role: string; slug: string; known: string }[];
  links: { known: string; legal?: string; firstPeriod?: string; posted?: string; successor?: string }[];
  ctx: DealContext;
  required: string[];
};

export const RAW_DEALS: RawDeal[] = [
  {
    id: 1, key: "txn:acme|borealis@2026-03-02", type: "acquisition", title: "Acme to acquire Borealis Systems", announced: "2026-03-02",
    participants: [{ role: "acquirer", name: "Acme Corp", ticker: "ACME" }, { role: "target", name: "Borealis Systems, Inc." }, { role: "bidder", name: "Dune Holdings" }],
    transitions: [
      { id: 11, state: "announced", eff: "2026-03-02", known: "2026-03-02", quote: "Acme Corp announced today that it has agreed to acquire Borealis Systems, Inc.", slug: "acme-8k-0302" },
      { id: 12, state: "definitive", eff: "2026-03-20", known: "2026-03-20", quote: "The parties entered into an Agreement and Plan of Merger dated March 20, 2026.", slug: "acme-8k-0320" },
      { id: 13, state: "contested", eff: "2026-05-02", known: "2026-05-02", quote: "Dune Holdings delivered an unsolicited proposal to the Borealis board.", slug: "borealis-8k-0502" },
      { id: 14, state: "approved", eff: "2026-06-10", known: "2026-06-11", quote: "Borealis stockholders approved the merger agreement.", slug: "borealis-8k-0611" },
      { id: 15, state: "closed", eff: "2026-07-31", known: "2026-08-01", quote: "On July 31, 2026, Acme completed its acquisition of Borealis.", slug: "acme-8k-0801" },
    ],
    versions: [
      { id: 101, no: 1, origin: "announced", known: "2026-03-02", currency: "USD", values: { price_per_share: "42.00", cash_amount: "1800000000", stock_amount: "600000000", exchange_ratio: "0.25", termination_fee: "85000000", financing_amount: "1800000000", unaffected_price: "33.00" }, conditions: "Closing is subject to Borealis stockholder approval and regulatory clearance.", unaffectedDate: "2026-02-27", unaffectedSource: "Closing price on the last trading day before the announcement", quote: "$42.00 per share, in cash and Acme shares.", slug: "acme-8k-0302" },
      { id: 102, no: 2, origin: "announced", known: "2026-04-14", currency: "USD", values: { price_per_share: "44.50", cash_amount: "1950000000", stock_amount: "600000000", earnout_amount: "100000000", exchange_ratio: "0.25", termination_fee: "85000000", financing_amount: "1800000000", unaffected_price: "33.00" }, conditions: "Closing is subject to Borealis stockholder approval and regulatory clearance.", unaffectedDate: "2026-02-27", unaffectedSource: "Closing price on the last trading day before the announcement", quote: "Amendment No. 1 raises the price to $44.50 per share and adds a $100 million earnout.", slug: "acme-8ka-0414" },
      { id: 103, no: 1, origin: "management_projection", known: "2026-03-02", currency: "USD", values: { expected_synergies: "150000000" }, quote: "Acme expects annual cost synergies of approximately $150 million.", slug: "acme-pr-0302" },
    ],
    documents: [
      { versionId: 1, role: "original", slug: "acme-8k-0302", known: "2026-03-02" },
      { versionId: 2, role: "exhibit", slug: "acme-pr-0302", known: "2026-03-02" },
      { versionId: 3, role: "amendment", slug: "acme-8ka-0414", known: "2026-04-14" },
      { versionId: 4, role: "proxy", slug: "borealis-proxy-0505", known: "2026-05-05" },
    ],
    links: [
      { known: "2026-08-01", legal: "2026-07-31", successor: "Acme Corp (Borealis is now a subsidiary)" },
      { known: "2026-08-04", posted: "2026-08-03" },
      { known: "2026-10-20", firstPeriod: "2026-09-30" },
    ],
    ctx: { acquirerShares: 1_000_000_000, targetShares: 400_000_000 },
    required: ["consideration", "currency"],
  },
  {
    id: 2, key: "txn:acme|dune-logistics@2026-03-02", type: "divestiture", title: "Acme to sell its Dune Logistics unit", announced: "2026-03-02",
    participants: [{ role: "seller", name: "Acme Corp", ticker: "ACME" }, { role: "buyer", name: "Harbor Freight Partners" }],
    transitions: [
      { id: 21, state: "announced", eff: "2026-03-02", known: "2026-03-02", quote: "Acme also announced the sale of its Dune Logistics unit to Harbor Freight Partners.", slug: "acme-8k-0302" },
      { id: 22, state: "definitive", eff: "2026-04-03", known: "2026-04-03", quote: "The parties signed a purchase agreement on April 3, 2026.", slug: "acme-8k-0403" },
      { id: 23, state: "closed", eff: "2026-06-15", known: "2026-06-16", quote: "The sale closed on June 15, 2026.", slug: "acme-8k-0616" },
    ],
    versions: [{ id: 201, no: 1, origin: "announced", known: "2026-03-02", currency: "USD", values: { cash_amount: "300000000" }, quote: "for $300 million in cash", slug: "acme-8k-0302" }],
    documents: [{ versionId: 5, role: "original", slug: "acme-8k-0302", known: "2026-03-02" }],
    links: [{ known: "2026-06-16", legal: "2026-06-15" }],
    ctx: {},
    required: ["consideration", "currency"],
  },
  {
    id: 3, key: "txn:acme-buyback@2026-01-15", type: "buyback", title: "Acme share repurchase program", announced: "2026-01-15",
    participants: [{ role: "issuer", name: "Acme Corp", ticker: "ACME" }],
    transitions: [{ id: 31, state: "announced", eff: "2026-01-15", known: "2026-01-15", quote: "The board authorized the repurchase of up to $1.0 billion of common stock.", slug: "acme-8k-0115" }],
    versions: [
      { id: 301, no: 1, origin: "announced", known: "2026-01-15", currency: "USD", values: { authorized_amount: "1000000000" }, quote: "up to $1.0 billion", slug: "acme-8k-0115" },
      { id: 302, no: 2, origin: "announced", known: "2026-06-30", currency: "USD", values: { authorized_amount: "1000000000", executed_amount: "250000000" }, quote: "repurchased $250 million of common stock during the quarter", slug: "acme-10q-0630" },
    ],
    documents: [{ versionId: 6, role: "original", slug: "acme-8k-0115", known: "2026-01-15" }],
    links: [],
    ctx: {},
    required: ["authorized_amount", "currency"],
  },
  {
    id: 4, key: "txn:acme|cinder@2026-02-20", type: "acquisition", title: "Acme approach to Cinder Retail (withdrawn)", announced: "2026-02-20",
    participants: [{ role: "acquirer", name: "Acme Corp", ticker: "ACME" }, { role: "target", name: "Cinder Retail Ltd." }],
    transitions: [
      { id: 41, state: "rumored", eff: "2026-02-04", known: "2026-02-04", quote: "A press report said Acme was exploring an approach to Cinder Retail.", slug: "press-0204", rumor: "Press report, 4 Feb, not confirmed by either company" },
      { id: 42, state: "announced", eff: "2026-02-20", known: "2026-02-20", quote: "Acme submitted a non-binding proposal to acquire Cinder Retail.", slug: "acme-8k-0220" },
      { id: 43, state: "terminated", eff: "2026-03-28", known: "2026-03-29", quote: "Acme withdrew its proposal and does not intend to pursue a transaction.", slug: "acme-8k-0329" },
    ],
    versions: [{ id: 401, no: 1, origin: "announced", known: "2026-02-20", currency: "USD", values: { price_per_share: "31.00" }, quote: "$31.00 per share", slug: "acme-8k-0220" }],
    documents: [{ versionId: 7, role: "original", slug: "acme-8k-0220", known: "2026-02-20" }],
    links: [],
    ctx: {},
    required: ["consideration", "currency"],
  },
];

const CONSIDERATION = ["cash_amount", "stock_amount", "debt_amount", "earnout_amount"] as const;

function buildVersions(raw: RawDeal, cutoff: number): TermVersion[] {
  const out: TermVersion[] = [];
  const last: Partial<Record<string, TermVersion>> = {};
  for (const v of raw.versions.filter((x) => Date.parse(at(x.known)) <= cutoff).sort((a, b) => a.no - b.no || a.id - b.id)) {
    const terms = TERM_NAMES.map((n) => (n in v.values ? { name: n, label: TERM_LABEL[n], state: "stated" as const, value: v.values[n] } : { name: n, label: TERM_LABEL[n], state: "not_stated" as const, value: null }));
    const prev = last[v.origin];
    const changes: TermVersion["changes"] = [];
    for (const t of terms) {
      const was = prev?.terms.find((p) => p.name === t.name)?.value ?? null;
      if (was !== t.value) changes.push({ field: t.name, label: t.label, old: was, new: t.value });
    }
    if ((prev?.closing_conditions ?? null) !== (v.conditions ?? null)) changes.push({ field: "closing_conditions", label: "Closing conditions", old: prev?.closing_conditions ?? null, new: v.conditions ?? null });
    const tv: TermVersion = {
      id: v.id, version_no: v.no, supersedes_id: prev?.id ?? null, origin: v.origin, known_at: at(v.known), event_effective_at: at(v.known),
      currency: v.currency, document_version_id: v.id, evidence_id: v.id, source_url: URL(v.slug), quote: v.quote, terms,
      closing_conditions: v.conditions ?? null, closing_conditions_state: v.conditions ? "stated" : "not_stated",
      unaffected_date: v.unaffectedDate ?? null, unaffected_source: v.unaffectedSource ?? null, changes, problems: [],
    };
    last[v.origin] = tv;
    out.push(tv);
  }
  return out;
}

function closingAsOf(raw: RawDeal, cutoff: number, closed: boolean): DealClosing | null {
  const known = raw.links.filter((l) => Date.parse(at(l.known)) <= cutoff).sort((a, b) => a.known.localeCompare(b.known));
  if (known.length === 0 && !closed) return null;
  const pick = <K extends "legal" | "firstPeriod" | "posted" | "successor">(k: K) => [...known].reverse().find((l) => l[k] != null)?.[k] ?? null;
  const legal = pick("legal");
  const first = pick("firstPeriod");
  const posted = pick("posted");
  const problems: string[] = [];
  if (legal && first && first < legal) problems.push("the first reporting period ends before the legal closing");
  if (legal && posted && posted < legal) problems.push("the broker posted the corporate action before the legal closing");
  const missing = closed ? ([["legal_closing_date", legal], ["first_reporting_period_end", first], ["broker_posting_date", posted]] as const).filter(([, v]) => v == null).map(([k]) => k) : [];
  return { legal_closing_date: legal, first_reporting_period_end: first, broker_posting_date: posted, successor_name: pick("successor"), successor_ticker: null, successor_issuer_id: null, missing, problems };
}

/** One deal as known at `cutoff` (ms); null when it was not yet known. `offer` is the owner's scenario. */
export function buildDeal(raw: RawDeal, cutoff: number, offer?: number | null): Deal | null {
  if (Date.parse(at(raw.announced)) > cutoff && !raw.transitions.some((t) => Date.parse(at(t.known)) <= cutoff)) return null;
  const all: DealTransition[] = raw.transitions.map((t) => ({
    id: t.id, state: t.state as DealTransition["state"], effective_at: at(t.eff), known_at: at(t.known), evidence_id: t.id, document_version_id: t.id,
    source_url: URL(t.slug), quote: t.quote, span_start: 0, span_end: t.quote.length, rumor_source: t.rumor ?? null, note: null,
  }));
  const res = resolveState(raw.type, all, cutoff);
  if (res.applied.length === 0 && res.rejected.length === 0) return null;
  const versions = buildVersions(raw, cutoff);
  const announced = versions.filter((v) => v.origin === "announced");
  const latest = announced.at(-1);
  const stated = (n: string) => latest?.terms.some((t) => t.name === n && t.state === "stated") ?? false;
  const missing = raw.required.filter((f) => (f === "consideration" ? !CONSIDERATION.some(stated) : f === "currency" ? !latest?.currency : !stated(f)));
  if (raw.type === "acquisition" && stated("price_per_share")) {
    if (!stated("unaffected_price")) missing.push("unaffected_price");
    if (latest && !latest.unaffected_date) missing.push("unaffected_date");
  }
  const announcedOn = raw.announced;
  const impacts = [];
  if (raw.type === "acquisition") {
    impacts.push(premiumImpact(latest, announcedOn));
    if (offer != null) impacts.push(premiumImpact(latest, announcedOn, offer));
    impacts.push(dilutionImpact(latest, raw.ctx));
    impacts.push(debtImpact(raw.type, latest, raw.ctx));
  }
  if (raw.type === "divestiture") impacts.push(debtImpact(raw.type, latest, raw.ctx));
  if (raw.type === "buyback") impacts.push(buybackImpact(latest));
  if (raw.type === "acquisition" || raw.type === "divestiture") impacts.push(synergyImpact(versions));
  const segments = CONSIDERATION.map((c) => {
    const t = latest?.terms.find((x) => x.name === c);
    return { kind: c.replace("_amount", "") as "cash" | "stock" | "debt" | "earnout", state: t?.state ?? ("not_stated" as const), amount: t?.value ?? null };
  });
  const knownTotal = segments.reduce((a, s) => a + Number(s.amount ?? 0), 0);
  const bridge: Bridge = {
    currency: latest?.currency ?? null, segments, known_total: String(knownTotal), unstated: segments.filter((s) => s.state === "not_stated").map((s) => s.kind),
    total_is_lower_bound: segments.some((s) => s.state === "not_stated"), cash_pct: null, stock_pct: null, term_version_id: latest?.id ?? null,
  };
  const sizeTerm = latest?.terms.find((t) => (t.name === "authorized_amount" || t.name === "financing_amount") && t.state === "stated");
  return {
    id: raw.id, transaction_key: raw.key, deal_type: raw.type, type_label: TYPE_LABEL[raw.type], title: raw.title, ticker: SAMPLE_DEAL_TICKER,
    announced_at: at(raw.announced), known_at: at(raw.announced), state: res.state as Deal["state"], confidence: confidence(res.state, res.rejected.length, missing.length),
    currency: latest?.currency ?? null, size: knownTotal > 0 ? String(knownTotal) : (sizeTerm?.value ?? null),
    participants: raw.participants.map((p) => ({ role: p.role, name: p.name, issuer_id: null, ticker: p.ticker ?? null, share_class: null })),
    transitions: res.applied, rejected_transitions: res.rejected, term_versions: versions, latest_version_id: latest?.id ?? null, bridge, impacts,
    documents: raw.documents.filter((d) => Date.parse(at(d.known)) <= cutoff).map((d) => ({ document_version_id: d.versionId, role: d.role, source_url: URL(d.slug), known_at: at(d.known), filed_at: at(d.known) })),
    missing_fields: missing.map((f) => ({ field: f, label: TERM_LABEL[f] ?? f })),
    closing: closingAsOf(raw, cutoff, res.state === "closed"),
  };
}

export function sampleDeals(cutoff: number, offer?: number | null): HoldingDeals {
  const deals = RAW_DEALS.map((d) => buildDeal(d, cutoff, offer)).filter((d): d is Deal => d !== null);
  return {
    ticker: SAMPLE_DEAL_TICKER,
    as_of: new Date(cutoff).toISOString(),
    deals,
    total_known: deals.length,
    excluded_unknown_size: 0,
    notes: deals.length === 0 ? ["No public deal is recorded for this holding by that date. That is not proof that none exists."] : [],
  };
}

export function samplePortfolioDeals(cutoff: number): PortfolioDeals {
  const weights = { ACME: 0.3, DUNE: 0.25, FJORD: 0.2, GLEN: 0.25 };
  const refs: DealRef[] = sampleDeals(cutoff).deals.map((d) => ({ ticker: "ACME", dealId: d.id, title: d.title, dealType: d.deal_type, state: d.state }));
  if (cutoff >= Date.parse(at("2026-05-20"))) refs.push({ ticker: "FJORD", dealId: 90, title: "Fjord Energy spin-off of its pipeline arm", dealType: "spin_off", state: "announced" });
  return portfolioExposure(weights, refs, new Date(cutoff).toISOString());
}
