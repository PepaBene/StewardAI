/**
 * The Ledger information architecture: pages grouped by job, not by feature (see
 * Ledger_Design_System_Redesign_Proposal.md section 3). Chat is deliberately not a cluster —
 * it stays a global Cmd/Ctrl+K + slide-over, reachable from anywhere, never a nav destination.
 */

export type ClusterPage = { readonly href: string; readonly label: string };

export type Cluster = {
  readonly id: "portfolio" | "research" | "oversight" | "config";
  readonly label: string;
  readonly description: string;
  /** Primary landing page: what clicking the cluster's own label opens. */
  readonly href: string;
  /** Pages shown in the cluster's menu. */
  readonly pages: readonly ClusterPage[];
  /** Detail-only routes that belong to this cluster but aren't a page of their own. */
  readonly extra?: readonly string[];
};

export const CLUSTERS: readonly Cluster[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    description: "What do I hold and how has it done.",
    href: "/",
    pages: [
      { href: "/", label: "Overview" },
      { href: "/holdings", label: "Holdings" },
      { href: "/transactions", label: "Transactions" },
      { href: "/watchlist", label: "Watchlist" },
      { href: "/funds", label: "Funds" },
    ],
  },
  {
    id: "research",
    label: "Research & Verification",
    description: "Is my thesis still true.",
    href: "/theses",
    pages: [
      { href: "/theses", label: "Theses" },
      { href: "/review", label: "Review queue" },
      { href: "/relationships", label: "Relationship review" },
      { href: "/research", label: "Research" },
      { href: "/screens", label: "Screens" },
      { href: "/macro", label: "Macro series" },
      { href: "/news", label: "News" },
      { href: "/investors", label: "13F investors" },
      { href: "/disclosures", label: "Public disclosures" },
      { href: "/disclosures/ownership", label: "Ownership" },
      { href: "/disclosures/insiders", label: "Insider activity" },
      { href: "/disclosures/network", label: "Customers and suppliers" },
      { href: "/disclosures/deals", label: "Deals and financing" },
      { href: "/disclosures/estimates", label: "Published estimates" },
      { href: "/disclosures/alerts", label: "Disclosure alerts" },
      { href: "/disclosures/adjudication", label: "Accuracy checks" },
      { href: "/glossary", label: "Glossary" },
    ],
    extra: ["/evidence"],
  },
  {
    id: "oversight",
    label: "Oversight",
    description: "What needs my attention and what happened.",
    href: "/reports",
    pages: [
      { href: "/escalations", label: "Escalations" },
      { href: "/reports", label: "Reports" },
      { href: "/reviews", label: "Reviews" },
      { href: "/activity", label: "Activity" },
      { href: "/data-sources", label: "Data sources" },
      { href: "/verification", label: "Verification" },
      { href: "/ai-decisions", label: "AI decisions" },
    ],
    extra: ["/jobs", "/status"],
  },
  {
    id: "config",
    label: "Config",
    description: "Strategy, theses, sources, notifications, research profiles, schedules and budget.",
    href: "/config",
    pages: [{ href: "/config", label: "Config" }],
  },
] as const;

/** Every page across every cluster, in cluster order — the flat list a "Go to" list needs. */
export const CLUSTER_PAGES: readonly (ClusterPage & { cluster: Cluster["label"] })[] = CLUSTERS.flatMap((c) =>
  c.pages.map((p) => ({ ...p, cluster: c.label })),
);

function hrefMatches(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Is `pathname` this exact page (or one of its sub-paths, e.g. a ticker or id detail page)? */
export function isPageActive(pageHref: string, pathname: string): boolean {
  return hrefMatches(pageHref, pathname);
}

/** Is `pathname` anywhere in this cluster — one of its pages, or a detail-only extra route? */
export function isClusterActive(cluster: Pick<Cluster, "pages" | "extra">, pathname: string): boolean {
  return cluster.pages.some((p) => hrefMatches(p.href, pathname)) || (cluster.extra ?? []).some((p) => hrefMatches(p, pathname));
}
