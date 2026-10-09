import { classOf, insidersKnown, netFromTransactions, type InsiderActivity, type InsiderTx } from "@/lib/disclosure/insiders";

/*
 * An invented company, “Acme Corp”, so the screen can be shown without touching real filings. The
 * sample is built to show every encoding: a purchase cluster, a sale under a plan, an exercise with a
 * same-day sale, a withholding line, a gift, an award and an unclassified row.
 */

export const SAMPLE_INSIDER_TICKER = "ACME";
/** The sample's “today”: the date control opens here so the 30, 90 and 365-day windows are populated. */
export const SAMPLE_INSIDER_DAY = "2026-06-30";

const SAMPLE_OUTSTANDING = 25_000_000;

type Row = Partial<InsiderTx> & Pick<InsiderTx, "accession" | "insider" | "role" | "economic_class" | "tx_date" | "filed">;

const tx = (r: Row, i: number): InsiderTx => {
  const shares = r.shares ?? null;
  const price = r.price ?? null;
  return {
    accession: r.accession,
    row_index: i,
    insider: r.insider,
    role: r.role,
    tx_date: r.tx_date,
    filed: r.filed,
    known_at: `${r.filed}T23:59:59Z`,
    economic_class: r.economic_class,
    classifier_class: r.classifier_class ?? r.economic_class,
    override_reason: r.override_reason ?? null,
    countable: r.countable ?? false,
    bundled: r.bundled ?? false,
    plan: r.plan ?? "not_plan",
    derivative: r.derivative ?? false,
    direct_indirect: r.direct_indirect ?? "D",
    shares,
    price,
    value: shares != null && price != null ? String(Number(shares) * Number(price)) : null,
    shares_after: r.shares_after ?? null,
    position_change_percent: r.position_change_percent ?? null,
    caveats: r.caveats ?? [],
    source_url: `https://www.sec.gov/Archives/edgar/data/acme/${r.accession}.txt`,
    footnote: r.footnote ?? null,
    exercise_price: r.exercise_price ?? null,
    exercisable_date: r.exercisable_date ?? null,
    expiration_date: r.expiration_date ?? null,
    underlying_security: r.underlying_security ?? null,
    underlying_shares: r.underlying_shares ?? null,
    plan_basis: r.plan_basis ?? null,
    plan_adopted: r.plan_adopted ?? null,
    percent_of_outstanding: shares != null ? String((Number(shares) / SAMPLE_OUTSTANDING) * 100) : null,
  };
};

const ROWS: Row[] = [
  { accession: "0000100001-26-000011", insider: "Dana Reyes (CEO)", role: "officer", economic_class: "open_market_buy", tx_date: "2026-06-04", filed: "2026-06-06", shares: "20000", price: "18.5", shares_after: "260000", position_change_percent: "8.3", countable: true },
  { accession: "0000100002-26-000012", insider: "Sam Okafor (director)", role: "director", economic_class: "open_market_buy", tx_date: "2026-06-09", filed: "2026-06-11", shares: "8000", price: "18.9", shares_after: "58000", position_change_percent: "16", countable: true, direct_indirect: "I", caveats: ["Held indirectly, through a trust."] },
  { accession: "0000100003-26-000013", insider: "Lee Chen (director)", role: "director", economic_class: "open_market_buy", tx_date: "2026-06-12", filed: "2026-06-16", shares: "5000", price: "19.2", shares_after: "30000", position_change_percent: "20", countable: true },
  { accession: "0000100004-26-000014", insider: "Priya Nair (CFO)", role: "officer", economic_class: "open_market_sell", tx_date: "2026-05-20", filed: "2026-05-22", shares: "12000", price: "20.1", shares_after: "88000", position_change_percent: "-12", countable: false, plan: "plan", plan_basis: "form_checkbox", plan_adopted: "2025-11-12", caveats: ["Reported under a Rule 10b5-1 trading plan."] },
  { accession: "0000100005-26-000015", insider: "Mo Alvarez (COO)", role: "officer", economic_class: "option_exercise", tx_date: "2026-05-28", filed: "2026-05-30", shares: "15000", price: "6", derivative: true, bundled: true, exercise_price: "6", exercisable_date: "2024-05-28", expiration_date: "2031-05-28", underlying_security: "Common stock", underlying_shares: "15000", footnote: "Options granted 28 May 2021; they vest over three years.", caveats: ["A derivative line: shares are not yet held."] },
  { accession: "0000100005-26-000015", insider: "Mo Alvarez (COO)", role: "officer", economic_class: "open_market_sell", tx_date: "2026-05-28", filed: "2026-05-30", shares: "15000", price: "20.4", shares_after: "41000", position_change_percent: "-26.8", bundled: true, plan: "unknown", caveats: ["The filing carries no plan indicator."] },
  { accession: "0000100006-26-000016", insider: "Dana Reyes (CEO)", role: "officer", economic_class: "grant_award", tx_date: "2026-03-02", filed: "2026-03-04", shares: "50000", price: "0", shares_after: "240000", caveats: ["Reported at a price of zero."] },
  { accession: "0000100006-26-000017", insider: "Dana Reyes (CEO)", role: "officer", economic_class: "tax_withholding", tx_date: "2026-03-15", filed: "2026-03-17", shares: "9000", price: "17.8", shares_after: "231000" },
  { accession: "0000100007-26-000018", insider: "Jo Whitfield (10% owner)", role: "ten_percent_owner", economic_class: "gift", tx_date: "2026-04-10", filed: "2026-04-14", shares: "30000", price: null, shares_after: "1900000" },
  { accession: "0000100008-26-000019", insider: "Jo Whitfield (10% owner)", role: "ten_percent_owner", economic_class: "open_market_sell", tx_date: "2026-02-18", filed: "2026-02-20", shares: "100000", price: "17.2", shares_after: "1930000", position_change_percent: "-4.9", countable: true, plan: "unknown", caveats: ["The filing carries no plan indicator."] },
  { accession: "0000100009-26-000020", insider: "Kim Park (officer)", role: "officer", economic_class: "unclassified", classifier_class: null, tx_date: "2026-06-18", filed: "2026-06-20", shares: "1000", price: "19.5", caveats: ["The classifier gave no class: not counted until reviewed."] },
  { accession: "0000100010-26-000021", insider: "Sam Okafor (director)", role: "director", economic_class: "open_market_buy", tx_date: "2026-07-14", filed: "2026-07-16", shares: "4000", price: "19.8", countable: true },
];

export const SAMPLE_INSIDER_ROWS: readonly InsiderTx[] = ROWS.map((r, i) => tx(r, i));

export function sampleInsiders(cutoffMs: number): InsiderActivity {
  const known = insidersKnown(SAMPLE_INSIDER_ROWS, cutoffMs);
  const net = netFromTransactions(SAMPLE_INSIDER_ROWS, cutoffMs);
  const counts: Record<string, number> = {};
  for (const t of known) counts[classOf(t)] = (counts[classOf(t)] ?? 0) + 1;
  return {
    ticker: SAMPLE_INSIDER_TICKER,
    as_of: new Date(cutoffMs).toISOString(),
    net: net.map((n) => ({
      window_days: n.window,
      role: n.role,
      buy_shares: "0",
      sell_shares: "0",
      net_shares: "0",
      buy_value: String(n.buyValue),
      sell_value: String(n.sellValue),
      net_value: String(n.netValue),
      trades: n.trades,
      insiders: n.insiders,
      plan_trades_excluded: n.planExcluded,
      plan_unknown_trades: n.planUnknown,
      bundled_sales_excluded: n.bundled,
    })),
    clusters: [],
    bundles: known.some((t) => t.bundled) ? [{ day: "2026-05-28", exercised: "15000", sold: "15000", proceeds: "306000", retained: "0" }] : [],
    class_counts: counts,
    notes: [],
    splits_applied: 0,
    context: {
      shares_outstanding: String(SAMPLE_OUTSTANDING),
      shares_outstanding_as_of: "2026-03-31",
      shares_outstanding_method: "cover_page",
      reported_share_count_change: "-0.012",
      events: [{ event_type: "earnings", event_state: "reported", announced_at: "2026-05-07", source_url: null }],
    },
    transactions: [...known].sort((a, b) => (b.tx_date ?? b.filed).localeCompare(a.tx_date ?? a.filed)),
  };
}
