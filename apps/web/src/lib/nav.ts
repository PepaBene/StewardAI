/**
 * The information architecture (docs/plans/ux_ui_final_redesign_plan.md §3): seven
 * destinations plus Settings, each with its tabs. Every route in `app/(app)` has exactly one
 * home: a tab here, a global route, or an entry in `REDIRECTS` (nav.test.ts checks this).
 *
 * Matching: a tab claims its own href and its sub-paths, plus any extra patterns in `match`.
 * A pattern ending in "/*" claims only the sub-paths ("/funds/*" claims "/funds/IE00…" but
 * not "/funds"). When several tabs claim a path, the longest pattern wins, so "/risk/whatif"
 * belongs to Decide and "/risk/tail" to Risk.
 *
 * Chat is not a destination: it stays a global Cmd/Ctrl+K entry and slide-over.
 */

export type NavTab = {
  readonly href: string;
  readonly label: string;
  /** Extra paths this tab owns (see the matching note above). */
  readonly match?: readonly string[];
};

export type NavGroup = { readonly label: string; readonly tabs: readonly NavTab[] };

export type DestinationId =
  | "portfolio"
  | "companies"
  | "research"
  | "markets"
  | "briefings"
  | "inbox"
  | "system"
  | "settings";

export type Destination = {
  readonly id: DestinationId;
  readonly label: string;
  readonly description: string;
  /** What clicking the destination opens. */
  readonly href: string;
  readonly tabs: readonly NavTab[];
  /** Settings shows its tabs as a grouped side menu. */
  readonly groups?: readonly NavGroup[];
};

const SETTINGS_GROUPS: readonly NavGroup[] = [
  {
    label: "Investment rules",
    tabs: [
      { href: "/config/strategy", label: "Strategy" },
      { href: "/config/theses", label: "Theses" },
      { href: "/config/holding-profiles", label: "Holding profiles" },
      { href: "/config/event-rules", label: "Event rules" },
      { href: "/config/checks", label: "Checks" },
    ],
  },
  {
    label: "Sources and data",
    tabs: [
      { href: "/config/news-sources", label: "News sources" },
      { href: "/config/calendar", label: "Exchange calendar" },
      { href: "/config/research-profiles", label: "Research profiles" },
      { href: "/config/verification", label: "Verification" },
    ],
  },
  {
    label: "Automation",
    tabs: [
      { href: "/config/jobs", label: "Jobs" },
      { href: "/config/budget", label: "Budget" },
      { href: "/config/prompts", label: "Prompts" },
      { href: "/config/notifications", label: "Notifications" },
      { href: "/config/autonomy", label: "Autonomy" },
    ],
  },
  {
    label: "History and export",
    tabs: [{ href: "/config/history", label: "History and export" }],
  },
];

export const DESTINATIONS: readonly Destination[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    description: "What you own, how it has done and what it is worth.",
    href: "/",
    tabs: [
      { href: "/", label: "Overview" },
      { href: "/holdings", label: "Holdings", match: ["/transactions", "/watchlist", "/funds"] },
      { href: "/performance", label: "Performance" },
      { href: "/portfolio/income", label: "Income" },
      { href: "/portfolio/valuation", label: "Valuation" },
      { href: "/xray", label: "Allocation" },
      { href: "/risk", label: "Risk" },
      {
        href: "/risk/whatif",
        label: "Decide",
        match: ["/risk/rebalance", "/risk/pretrade", "/risk/deposit", "/xray/proposed"],
      },
    ],
  },
  {
    id: "companies",
    label: "Companies",
    description: "One company or fund at a time.",
    href: "/companies",
    tabs: [
      { href: "/companies", label: "Companies", match: ["/holdings/*"] },
      { href: "/funds/research", label: "Funds", match: ["/funds/*"] },
    ],
  },
  {
    id: "research",
    label: "Research",
    description: "Your reasons, your research and new ideas.",
    href: "/theses",
    tabs: [
      { href: "/theses", label: "Theses", match: ["/thesis"] },
      { href: "/research", label: "Reports" },
      { href: "/discover", label: "Discover", match: ["/screens"] },
      { href: "/journal", label: "Journal" },
      { href: "/insights", label: "What changed" },
    ],
  },
  {
    id: "markets",
    label: "Markets",
    description: "News, filings and the economy around your holdings.",
    href: "/news",
    tabs: [
      { href: "/news", label: "News" },
      { href: "/macro", label: "Macro" },
      { href: "/disclosures", label: "Filings" },
      { href: "/investors", label: "13F investors" },
    ],
  },
  {
    id: "briefings",
    label: "Briefings",
    description: "The digests and reviews written for you.",
    href: "/reports",
    tabs: [
      { href: "/reports", label: "Daily and weekly" },
      { href: "/reviews", label: "Monthly reviews" },
    ],
  },
  {
    id: "inbox",
    label: "Inbox",
    description: "Everything that waits for your decision.",
    href: "/inbox",
    tabs: [{ href: "/inbox", label: "Inbox", match: ["/news/ops"] }],
  },
  {
    id: "system",
    label: "System",
    description: "What the app did, what it cost and whether its data holds up.",
    href: "/engine-ledger",
    tabs: [
      { href: "/engine-ledger", label: "Autonomy", match: ["/autopilot"] },
      { href: "/activity", label: "Activity", match: ["/jobs"] },
      { href: "/data-sources", label: "Data sources" },
      { href: "/quality", label: "Data quality", match: ["/disclosures/coverage"] },
      { href: "/verification", label: "Verification" },
      { href: "/release", label: "Release" },
      { href: "/ai-decisions", label: "AI decisions" },
      { href: "/status", label: "API status" },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    description: "Your rules, sources, schedules and budget.",
    href: "/config/strategy",
    tabs: SETTINGS_GROUPS.flatMap((g) => g.tabs),
    groups: SETTINGS_GROUPS,
  },
];

/** Routes that belong to no destination: reachable from everywhere. */
export const GLOBAL_ROUTES: readonly string[] = ["/chat", "/glossary", "/methods", "/evidence", "/dev"];

/**
 * Old URLs and their new home. Wired into next.config.ts (`redirects()`); query strings pass
 * through, so `/review?ticker=X` lands on `/inbox?kind=figures&ticker=X`. Temporary (307)
 * until the IA migration finishes (plan R7), so a wrong target is never cached for good.
 */
export const REDIRECTS: readonly { readonly source: string; readonly destination: string }[] = [
  { source: "/config", destination: "/config/strategy" },
  { source: "/holdings/:ticker", destination: "/companies/:ticker" },
  { source: "/escalations", destination: "/inbox?kind=escalations" },
  { source: "/escalations/:id", destination: "/inbox?kind=escalations&item=:id" },
  { source: "/review", destination: "/inbox?kind=figures" },
  { source: "/relationships", destination: "/inbox?kind=relationships" },
  { source: "/news/disputed", destination: "/inbox?kind=disputed" },
  { source: "/disclosures/alerts", destination: "/inbox?kind=disclosure-alerts" },
  { source: "/disclosures/adjudication", destination: "/inbox?kind=accuracy" },
  { source: "/disclosures/deals/parties", destination: "/inbox?kind=deal-parties" },
];

/** Where an old URL now lives (the `REDIRECTS` target), or null when it has not moved. */
export function redirectTarget(pathname: string): string | null {
  const path = pathname.split(/[?#]/)[0];
  for (const r of REDIRECTS) {
    const pattern = new RegExp(`^${r.source.replace(/:(\w+)/g, "(?<$1>[^/]+)")}$`);
    const m = pattern.exec(path);
    if (m) return r.destination.replace(/:(\w+)/g, (_, k: string) => m.groups?.[k] ?? "");
  }
  return null;
}

type Claim = { destination: Destination; tab: NavTab; score: number };

function patternScore(pattern: string, pathname: string): number {
  if (pattern.endsWith("/*")) {
    const base = pattern.slice(0, -2);
    return pathname.startsWith(`${base}/`) ? base.length + 1 : -1;
  }
  if (pattern === "/") return pathname === "/" ? 1 : -1;
  return pathname === pattern || pathname.startsWith(`${pattern}/`) ? pattern.length : -1;
}

/** The tab (and its destination) that owns `pathname`, or null for a global route. */
export function resolveNav(pathname: string): { destination: Destination; tab: NavTab } | null {
  const raw = pathname.split(/[?#]/)[0] || "/";
  const path = (redirectTarget(raw) ?? raw).split(/[?#]/)[0];
  let best: Claim | null = null;
  for (const destination of DESTINATIONS) {
    for (const tab of destination.tabs) {
      for (const pattern of [tab.href, ...(tab.match ?? [])]) {
        const score = patternScore(pattern, path);
        if (score > (best?.score ?? 0)) best = { destination, tab, score };
      }
    }
  }
  return best ? { destination: best.destination, tab: best.tab } : null;
}

export function isDestinationActive(id: DestinationId, pathname: string): boolean {
  return resolveNav(pathname)?.destination.id === id;
}

export function isTabActive(tab: NavTab, pathname: string): boolean {
  return resolveNav(pathname)?.tab.href === tab.href;
}

/** Every tab with its destination's label: the flat list a "Go to" list needs. */
export const NAV_PAGES: readonly (NavTab & { destination: Destination["label"] })[] = DESTINATIONS.flatMap((d) =>
  d.tabs.map((t) => ({ ...t, destination: d.label })),
);
