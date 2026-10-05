import { CONFIG_DOMAINS, DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { humanizeMetric } from "@/lib/evidence/confidence";
import { reviewQueueHref } from "@/lib/evidence/review";
import { CLUSTERS, isClusterActive } from "@/lib/nav";
import { kpiHref } from "@/lib/portfolio/kpi-anchor";

/*
 * Command palette entries: every primary destination, the config editors, each holding,
 * and the actions that re-run a check. Matching is plain word containment, so it is
 * predictable: every typed word must appear in the label or the keywords.
 */

export type PaletteAction =
  | { kind: "earnings-check"; ticker: string }
  | { kind: "portfolio-sync" }
  | { kind: "news-ingest" }
  | { kind: "digest"; period: "daily" | "weekly" }
  | { kind: "thesis-verdicts" }
  | { kind: "reference-data"; ticker?: string }
  | { kind: "price-history" }
  | { kind: "holiday-refresh" }
  | { kind: "macro-collect" }
  | { kind: "monthly-review" }
  | { kind: "ownership-13f" }
  | { kind: "research"; ticker: string };

export type PaletteGroup = (typeof CLUSTERS)[number]["label"] | "Copilot" | "Holdings" | "Actions";

export type PaletteCommand = {
  id: string;
  label: string;
  group: PaletteGroup;
  keywords?: string;
  href?: string;
  action?: PaletteAction;
  /** Opens a panel on the current page instead of navigating. */
  ui?: "open-chat";
};

/**
 * Which of the five-cluster IA's groups a destination belongs to (Ledger_Design_System_
 * Redesign_Proposal.md section 3), so the palette's trailing label says "Oversight" or
 * "Research & Verification" instead of a blanket "Go to". Chat has no cluster: it is
 * ambient, so its entries are labelled "Copilot" directly by their own definition.
 */
function clusterOf(href: string): PaletteGroup {
  const path = href.split(/[?#]/)[0];
  if (path === "/chat") return "Copilot";
  const cluster = CLUSTERS.find((c) => isClusterActive(c, path));
  return cluster?.label ?? "Config";
}

/** Primary destinations that aren't already a richly-keyworded entry below. */
const PRIMARY_PAGES = [
  { href: "/", label: "Overview", keywords: "portfolio value allocation drift alerts" },
  { href: "/holdings", label: "Holdings", keywords: "positions ticker thesis verdicts news evidence" },
  { href: "/news", label: "News", keywords: "feed severity confidence source tier" },
  { href: "/screens", label: "Screens", keywords: "screener filter margin roic growth leverage cash conversion find issuers" },
  { href: "/macro", label: "Macro series", keywords: "inflation gdp rates indicators revisions vintage country small multiples" },
  { href: "/research", label: "Research", keywords: "reports research this ticker" },
  { href: "/reports", label: "Reports", keywords: "digest archive monthly reviews impact notes full text" },
  { href: "/activity", label: "Activity", keywords: "job log coverage gaps cost budget" },
  { href: "/data-sources", label: "Data sources", keywords: "providers coverage quota access free api keys paid denied" },
  { href: "/verification", label: "Source gates", keywords: "source gates signals coverage shadow report zero cost domain reconciliation publish audit precision" },
  { href: "/relationships", label: "Relationship review", keywords: "customer supplier suppliers contradictions disputed disclosures company graph edges corporate actions split dividend events reconciliation confirm reject dispute" },
  { href: "/funds", label: "Funds", keywords: "overlap look-through factsheet etf aggregate issuer exposure unknown weight expense ratio" },
  { href: "/config", label: "Config", keywords: "strategy theses sources notifications research profiles schedules budget" },
] as const;

export const CONFIG_PAGES = [
  { href: "/config/strategy", label: "Strategy", keywords: "targets allocation bands rules categories classifications currency drift" },
  { href: "/config/jobs", label: "Jobs", keywords: "schedules cron timezone models step caps worker benchmarks prices reference wikidata holidays escalation macro eurostat fred ecb monitoring chat monthly review 13f ownership tracked managers" },
  { href: "/config/theses", label: "Theses", keywords: "kpi thresholds exit" },
  { href: "/config/holding-profiles", label: "Holding profiles", keywords: "ir aliases competitors fiscal stooq price symbol" },
  { href: "/config/calendar", label: "Exchange calendar", keywords: "holidays closures exchanges mic market closed nager public holiday" },
  { href: "/config/news-sources", label: "News sources", keywords: "feeds rss tiers outlets rns eqs edgar full-text central bank macro allow list" },
  { href: "/config/event-rules", label: "Event rules", keywords: "severity taxonomy price move threshold macro surprise bands announcement types rns eqs" },
  { href: "/config/notifications", label: "Notifications", keywords: "alerts telegram email quiet hours channels weekends holidays monitoring system alerts replay preview" },
  { href: "/config/verification", label: "Verification", keywords: "sanity bounds tolerance corroboration trust penalty reward demote claim verifier strip mark numeric check" },
  { href: "/config/research-profiles", label: "Research profiles", keywords: "research sections depth model effort searches tiers peers valuation language quick standard" },
  { href: "/config/prompts", label: "Prompts", keywords: "system prompts ai instructions tone override customize reset default evals claude" },
] as const;

/** Pages that are not a section or a config editor. */
export const OTHER_PAGES = [
  { href: "/theses", label: "Thesis board", keywords: "theses kpi verdicts pass breach unknown status holdings overview" },
  { href: "/review", label: "Review queue", keywords: "held quarantined figures facts accept correct reject override validator reconciliation" },
  { href: "/transactions", label: "Transactions and dividends", keywords: "orders dividends cash deposits withdrawals history holdings" },
  { href: "/glossary", label: "Glossary", keywords: "terms definitions metrics meaning help" },
  { href: "/watchlist", label: "Watchlist", keywords: "watch list buy sell target ticker idea candidates" },
  { href: "/investors", label: "Investors", keywords: "institutional managers tracked holders funds ownership filings" },
  { href: "/config", label: "Config: Export or import", keywords: "backup json file git" },
  { href: "/status#reference-gaps", label: "Reference data gaps", keywords: "identity unresolved figi isin lei stooq prices missing symbol coverage" },
  { href: "/news/disputed", label: "Disputed stories", keywords: "news contradicted conflicting sources resolve confirm false side by side" },
  { href: "/escalations", label: "Impact notes (escalations)", keywords: "escalations reports thesis kpi exit price move confirmed stories" },
  { href: "/reviews", label: "Monthly reviews", keywords: "reports review compliance rules breach pass proposed actions month" },
  { href: "/reports?type=research", label: "Search reports", keywords: "archive full text search digests reviews research impact notes find phrase ticker" },
  { href: "/activity#job-log", label: "Job log", keywords: "activity jobs runs cost failed step history" },
  { href: "/activity#coverage", label: "Coverage gaps", keywords: "activity holdings news prices missed monitoring gap" },
  { href: "/activity#alerts", label: "Monitoring alerts", keywords: "activity system failed jobs missed runs coverage alerts" },
  { href: "/activity#source-trust", label: "Source trust", keywords: "activity feedback trust score demoted outlets" },
  { href: "/activity#eval-candidates", label: "Eval candidates", keywords: "activity evaluation golden labelled set feedback export evals" },
  { href: "/status", label: "API status", keywords: "health backend version" },
  // Chat's full page: the header's slide-over links here, and it stays reachable by typing
  // "chat" even though it has no nav cluster of its own.
  { href: "/chat", label: "Chat (full page)", keywords: "assistant copilot conversation ask question" },
] as const;

/** A job number, with or without a leading "#" or "job": open that job run (the list is on Activity). */
export function jobCommands(query: string): PaletteCommand[] {
  const match = /^(?:job\s*)?#?(\d{1,12})$/i.exec(query.trim());
  if (!match) return [];
  const id = Number(match[1]);
  if (!Number.isSafeInteger(id) || id <= 0) return [];
  return [{ id: `job:${id}`, label: `Job #${id}`, group: "Go to", keywords: "job run steps", href: `/jobs/${id}` }];
}

export function baseCommands(): PaletteCommand[] {
  return [
    ...PRIMARY_PAGES.map((p) => ({
      id: `nav:${p.href}`,
      label: p.label,
      group: clusterOf(p.href),
      keywords: p.keywords,
      href: p.href,
    })),
    ...CONFIG_PAGES.map((p) => ({
      id: `nav:${p.href}`,
      label: `Config: ${p.label}`,
      group: clusterOf(p.href),
      keywords: p.keywords,
      href: p.href,
    })),
    ...OTHER_PAGES.map((p) => ({
      id: `nav:${p.href}`,
      label: p.label,
      group: clusterOf(p.href),
      keywords: p.keywords,
      href: p.href,
    })),
    ...DOMAIN_SLUGS.map((slug) => ({
      id: `nav:${historyHref(slug)}`,
      label: `History: ${CONFIG_DOMAINS[slug].label}`,
      group: clusterOf(historyHref(slug)),
      keywords: "config versions diff rollback undo",
      href: historyHref(slug),
    })),
    { id: "ui:chat", label: "Open chat", group: "Copilot", keywords: "ask question assistant copilot conversation panel", ui: "open-chat" },
    { id: "act:sync", label: "Sync portfolio now", group: "Actions", keywords: "trading212 refresh", action: { kind: "portfolio-sync" } },
    { id: "act:news", label: "Check news now", group: "Actions", keywords: "ingest feeds run", action: { kind: "news-ingest" } },
    { id: "act:verdicts", label: "Re-run thesis verdicts", group: "Actions", keywords: "kpi judge", action: { kind: "thesis-verdicts" } },
    { id: "act:daily", label: "Write daily digest now", group: "Actions", keywords: "report send", action: { kind: "digest", period: "daily" } },
    { id: "act:weekly", label: "Write weekly digest now", group: "Actions", keywords: "report send", action: { kind: "digest", period: "weekly" } },
    { id: "act:reference", label: "Refresh reference data now", group: "Actions", keywords: "identity openfigi esma firds gleif wikidata structure all holdings", action: { kind: "reference-data" } },
    { id: "act:prices", label: "Collect price history now", group: "Actions", keywords: "stooq prices fx risk benchmark", action: { kind: "price-history" } },
    { id: "act:holidays", label: "Refresh public holidays now", group: "Actions", keywords: "calendar nager exchange closures", action: { kind: "holiday-refresh" } },
    { id: "act:macro", label: "Collect macro data now", group: "Actions", keywords: "eurostat fred ecb inflation rates surprise", action: { kind: "macro-collect" } },
    { id: "act:review", label: "Run monthly review now", group: "Actions", keywords: "report compliance rules actions month", action: { kind: "monthly-review" } },
    { id: "act:13f", label: "Load 13F ownership data now", group: "Actions", keywords: "sec form 13f institutional holders managers", action: { kind: "ownership-13f" } },
  ];
}

export function holdingCommands(holdings: { ticker: string; name?: string | null }[]): PaletteCommand[] {
  return holdings.flatMap((h) => {
    const name = h.name ?? h.ticker;
    return [
      {
        id: `holding:${h.ticker}`,
        label: name,
        group: "Holdings" as const,
        keywords: `${h.ticker} holding thesis`,
        href: `/holdings/${encodeURIComponent(h.ticker)}`,
      },
      {
        id: `review:${h.ticker}`,
        label: `Review queue: ${name}`,
        group: "Holdings" as const,
        keywords: `${h.ticker} held quarantined figures accept correct reject`,
        href: reviewQueueHref(h.ticker),
      },
      {
        id: `reference:${h.ticker}`,
        label: `Refresh reference data: ${name}`,
        group: "Actions" as const,
        keywords: `${h.ticker} identity figi lei gleif wikidata structure`,
        action: { kind: "reference-data" as const, ticker: h.ticker },
      },
      {
        id: `research:${h.ticker}`,
        label: `Research: ${name}`,
        group: "Actions" as const,
        keywords: `${h.ticker} research report deep default profile`,
        action: { kind: "research" as const, ticker: h.ticker },
      },
      {
        id: `earnings:${h.ticker}`,
        label: `Re-run earnings check: ${name}`,
        group: "Actions" as const,
        keywords: `${h.ticker} earnings results extract verdict`,
        action: { kind: "earnings-check" as const, ticker: h.ticker },
      },
    ];
  });
}

/**
 * One entry per (holding, KPI): typing a metric and a ticker ("revenue growth AAPL") jumps
 * straight to that KPI's verdict row on the holding page, not just the holding itself — the
 * open opportunity Linear's Cmd+K research flagged (§2.2 of the Ledger II proposal): depth,
 * not existence. `rows` is theses/board's own data, already the one place that lists every
 * holding's KPIs; this adds no new fetch of its own.
 */
export function kpiCommands(
  rows: readonly { ticker: string; name: string | null; kpis: readonly { kpi: { id: string; metric: string } }[] }[],
): PaletteCommand[] {
  return rows.flatMap((row) => {
    const name = row.name ?? row.ticker;
    return row.kpis.map(({ kpi }) => ({
      id: `kpi:${row.ticker}:${kpi.id}`,
      label: `${humanizeMetric(kpi.metric)} · ${name}`,
      group: "Holdings" as const,
      keywords: `${row.ticker} ${kpi.metric.replaceAll("_", " ")} kpi verdict pass breach unknown`,
      href: kpiHref(row.ticker, kpi.id),
    }));
  });
}

/**
 * One entry per (holding, metric): typing a metric and a ticker also resolves straight to
 * that figure's evidence page — the other half of §4.7's "KPI cell or fact" ask, alongside
 * kpiCommands(). `index` is GET /facts/index's own data: the newest fact per (ticker,
 * metric) across every holding, small and cheap because it exists only to back this
 * search, never a claim surface of its own.
 */
export function factCommands(
  index: readonly { ticker: string; name: string | null; metric: string; fact_id: number; period: string }[],
): PaletteCommand[] {
  return index.map((entry) => {
    const name = entry.name ?? entry.ticker;
    return {
      id: `fact:${entry.ticker}:${entry.metric}`,
      label: `${humanizeMetric(entry.metric)} · ${name} (figure)`,
      group: "Holdings" as const,
      keywords: `${entry.ticker} ${entry.metric.replaceAll("_", " ")} figure fact extracted evidence source`,
      href: `/evidence/${entry.fact_id}`,
    };
  });
}

/** Per-holding entries, shown only once something is typed. */
const PER_HOLDING = ["earnings:", "review:", "reference:", "research:", "kpi:", "fact:"];

export function filterCommands(commands: PaletteCommand[], query: string, limit = 12): PaletteCommand[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) {
    return commands.filter((c) => !PER_HOLDING.some((prefix) => c.id.startsWith(prefix))).slice(0, limit);
  }
  const scored = commands
    .map((c) => {
      const label = c.label.toLowerCase();
      const haystack = `${label} ${(c.keywords ?? "").toLowerCase()}`;
      if (!words.every((w) => haystack.includes(w))) return null;
      const score = (label.startsWith(words[0]) ? 0 : 1) + (words.every((w) => label.includes(w)) ? 0 : 1);
      return { c, score };
    })
    .filter((x): x is { c: PaletteCommand; score: number } => x !== null);
  scored.sort((a, b) => a.score - b.score);
  return scored.slice(0, limit).map((x) => x.c);
}
