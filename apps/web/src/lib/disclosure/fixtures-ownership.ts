import type { HolderBook, Lane, LaneEntry, OwnershipView, StakeEvent } from "./ownership";
import { LANE_CAVEAT, LANE_ORDER } from "./ownership";

/*
 * Sample ownership for the invented "Acme Corp" (ticker ACME), replayed at a cutoff through the same
 * rules the backend applies: only filings known by the cutoff count, a 13F RESTATEMENT replaces the
 * manager's report once known, and each lane is totalled on its own. Nothing here is real.
 */

export const SAMPLE_TICKER = "ACME";
const DENOMINATOR = 10_000_000;
const DENOMINATOR_KNOWN = "2026-02-20T23:59:59Z";

/** `absent`: the manager filed a report for the period that no longer lists the holding (an exit). */
type Report = { period: string; filed: string; known_at: string; shares: number | null; restatement?: boolean; absent?: boolean };
const eod = (day: string) => `${day}T23:59:59Z`;
const r = (period: string, filed: string, shares: number | null, restatement = false): Report => ({ period, filed, known_at: eod(filed), shares, restatement });
const exited = (period: string, filed: string): Report => ({ period, filed, known_at: eod(filed), shares: null, absent: true });

const MANAGERS: Record<string, Report[]> = {
  "Alpha Capital": [r("2025-09-30", "2025-11-10", 1_100_000), r("2025-12-31", "2026-02-10", 1_250_000)],
  "Beta Partners": [r("2025-09-30", "2025-11-12", 800_000), r("2025-12-31", "2026-02-12", 900_000), r("2025-12-31", "2026-04-20", 700_000, true)],
  "Gamma Advisors": [r("2025-09-30", "2025-11-14", 500_000), r("2025-12-31", "2026-02-13", 520_000)],
  "Delta Funds": [r("2025-09-30", "2025-11-13", 420_000), exited("2025-12-31", "2026-02-13")],
  "Epsilon Management": [r("2025-12-31", "2026-02-14", 610_000)], // first appears in Q4
};

export const SAMPLE_STAKES: StakeEvent[] = [
  { holder: "Founders Group", form: "SC 13G", percent: 5.2, known_at: eod("2025-02-14"), event_date: "2024-12-31", amendment: null },
  { holder: "Founders Group", form: "SC 13G/A", percent: 6.8, known_at: eod("2026-02-14"), event_date: "2025-12-31", amendment: 1 },
  { holder: "Founders Group", form: "SC 13D", percent: 9.1, known_at: eod("2026-04-03"), event_date: "2026-03-25", amendment: null },
  { holder: "Founders Group", form: "SC 13D/A", percent: 10.5, known_at: eod("2026-06-20"), event_date: "2026-06-12", amendment: 1 },
];

const FUND = { name: "Acme Growth Fund", shares: 180_000, report: "2025-10-31", filed: "2025-12-29" };

const day = (iso: string) => iso.slice(0, 10);
const diff = (a: string, b: string) => Math.round((Date.parse(day(b)) - Date.parse(day(a))) / 86_400_000);

/** The manager's report for a period as known at the cutoff (a restatement replaces the original). */
function reportAt(rows: Report[], period: string, cutoff: number): Report | null {
  const seen = rows.filter((x) => x.period === period && Date.parse(x.known_at) <= cutoff).sort((a, b) => Date.parse(a.known_at) - Date.parse(b.known_at));
  return seen.length ? seen[seen.length - 1] : null;
}

export function managerBook(period: string, cutoffMs: number): HolderBook {
  const book: HolderBook = {};
  for (const [name, rows] of Object.entries(MANAGERS)) {
    const rep = reportAt(rows, period, cutoffMs);
    if (rep && !rep.absent && rep.shares != null) book[name] = rep.shares;
  }
  return book;
}

const total = (xs: (number | null)[]) => (xs.some((x) => x != null) ? xs.reduce<number>((a, b) => a + (b ?? 0), 0) : null);

function lane(key: Lane["key"], entries: LaneEntry[], denominator: number | null, notes: string[] = []): Lane {
  const known = entries.map((e) => (e.shares == null ? null : Number(e.shares)));
  const t = total(known);
  const pct = t != null && denominator ? (t / denominator) * 100 : null;
  const exceeds = t != null && denominator != null && t > denominator;
  const n = [...notes];
  if (known.some((x) => x == null)) n.push("Some entries report no share count and are not in the total");
  if (exceeds) n.push("The lane exceeds the denominator: the figures use different bases.");
  if (denominator == null) n.push("No share-count denominator is known, so no remainder is shown.");
  return {
    key,
    title: { institutional_13f: "Institutional managers (13F)", control_13dg: "Reported control stakes (13D/G)", fund_nport: "Fund holdings (N-PORT)", national_voting: "National voting-rights notices" }[key],
    basis: LANE_CAVEAT[key],
    status: entries.length ? "covered" : "no_data",
    entries,
    total_shares: t == null ? null : String(t),
    percent_of_denominator: pct == null ? null : pct.toFixed(4),
    remainder_shares: t != null && denominator != null && !exceeds ? String(denominator - t) : null,
    remainder_percent: pct != null && !exceeds ? (100 - pct).toFixed(4) : null,
    overlaps_with: [],
    notes: entries.length ? n : ["Nothing was known in this lane at the chosen date."],
  };
}

const asStrings = (b: HolderBook) => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, String(v)]));

/** The last two quarters any manager had reported by the cutoff, or null while only one is known. */
function sampleTrend(cutoffMs: number): OwnershipView["trend"] {
  const prev = managerBook("2025-09-30", cutoffMs);
  const cur = managerBook("2025-12-31", cutoffMs);
  const curKnown = Object.values(MANAGERS).some((rows) => rows.some((x) => x.period === "2025-12-31" && Date.parse(x.known_at) <= cutoffMs));
  if (!curKnown || Object.keys(prev).length === 0) return null;
  return { prev_period: "2025-09-30", cur_period: "2025-12-31", prev: asStrings(prev), cur: asStrings(cur) };
}

export function sampleOwnership(cutoffMs: number): OwnershipView {
  const denominator = cutoffMs >= Date.parse(DENOMINATOR_KNOWN) ? DENOMINATOR : null;

  const f13: LaneEntry[] = [];
  for (const [name, rows] of Object.entries(MANAGERS)) {
    const periods = [...new Set(rows.map((x) => x.period))].sort().reverse();
    for (const p of periods) {
      const rep = reportAt(rows, p, cutoffMs);
      if (!rep) continue;
      // The newest report decides: one that no longer lists the holding is an exit, not a gap to fill from an older quarter.
      if (rep.absent) break;
      f13.push({ holder: name, shares: rep.shares == null ? null : String(rep.shares), reported_percent: null, report_date: p, filed: rep.filed, known_at: rep.known_at, lag_days: diff(p, rep.filed), amended: !!rep.restatement });
      break;
    }
  }
  f13.sort((a, b) => Number(b.shares ?? 0) - Number(a.shares ?? 0));

  const chain = SAMPLE_STAKES.filter((e) => Date.parse(e.known_at) <= cutoffMs);
  const latest = chain[chain.length - 1];
  const stakes: LaneEntry[] = latest
    ? [{ holder: latest.holder, shares: latest.percent == null ? null : String((latest.percent / 100) * DENOMINATOR), reported_percent: latest.percent == null ? null : String(latest.percent), report_date: latest.event_date, filed: day(latest.known_at), known_at: latest.known_at, lag_days: latest.event_date ? diff(latest.event_date, latest.known_at) : null, amended: latest.form.endsWith("/A") }]
    : [];

  const fundKnown = eod(FUND.filed);
  const funds: LaneEntry[] = Date.parse(fundKnown) <= cutoffMs ? [{ holder: FUND.name, shares: String(FUND.shares), reported_percent: null, report_date: FUND.report, filed: FUND.filed, known_at: fundKnown, lag_days: diff(FUND.report, FUND.filed), amended: false }] : [];

  const lanes: Lane[] = LANE_ORDER.map((k) => {
    if (k === "national_voting") return { ...lane(k, [], denominator), status: "not_covered", notes: ["No source we can read covers this lane for this issuer."] };
    const built = lane(k, { institutional_13f: f13, control_13dg: stakes, fund_nport: funds }[k], denominator, k === "control_13dg" ? ["The total is a floor: each filing shows its largest reporting person."] : []);
    return { ...built, overlaps_with: k === "institutional_13f" ? ["fund_nport", "control_13dg"] : k === "fund_nport" ? ["institutional_13f"] : ["institutional_13f"] };
  });
  const live = lanes.filter((l) => l.status === "covered");
  const warnings: string[] = [];
  for (const a of live) for (const b of live) if (a.key < b.key && a.overlaps_with.includes(b.key)) warnings.push(`${a.title} and ${b.title} overlap; not added`);
  if (denominator == null) warnings.push("No denominator: percentages and remainders are unavailable");
  return {
    ticker: SAMPLE_TICKER,
    as_of: new Date(cutoffMs).toISOString(),
    denominator: denominator == null ? null : String(denominator),
    denominator_as_of: denominator == null ? null : "2025-12-31",
    denominator_method: denominator == null ? null : "dei_instance",
    lanes,
    warnings,
    trend: sampleTrend(cutoffMs),
    manager_renames: [],
  };
}
