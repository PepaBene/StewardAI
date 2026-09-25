import { CONFIG_DOMAINS, DOMAIN_SLUGS, historyHref } from "@/lib/config/domains";
import { SECTIONS, sectionHref } from "@/lib/nav";

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
  | { kind: "thesis-verdicts" };

export type PaletteCommand = {
  id: string;
  label: string;
  group: "Go to" | "Holdings" | "Actions";
  keywords?: string;
  href?: string;
  action?: PaletteAction;
};

export const CONFIG_PAGES = [
  { href: "/config/strategy", label: "Strategy", keywords: "targets allocation bands rules categories classifications currency drift" },
  { href: "/config/jobs", label: "Jobs", keywords: "schedules cron timezone models step caps worker" },
  { href: "/config/theses", label: "Theses", keywords: "kpi thresholds exit" },
  { href: "/config/holding-profiles", label: "Holding profiles", keywords: "ir aliases competitors fiscal" },
  { href: "/config/news-sources", label: "News sources", keywords: "feeds rss tiers outlets" },
  { href: "/config/event-rules", label: "Event rules", keywords: "severity taxonomy" },
  { href: "/config/notifications", label: "Notifications", keywords: "alerts telegram email quiet hours channels" },
] as const;

/** Pages that are not a section or a config editor. */
export const OTHER_PAGES = [
  { href: "/transactions", label: "Transactions and dividends", keywords: "orders dividends cash deposits withdrawals history holdings" },
  { href: "/config", label: "Config: Export or import", keywords: "backup json file git" },
] as const;

export function baseCommands(): PaletteCommand[] {
  return [
    ...SECTIONS.map((s) => ({
      id: `nav:${s.slug || "overview"}`,
      label: s.label,
      group: "Go to" as const,
      keywords: s.description,
      href: sectionHref(s),
    })),
    ...CONFIG_PAGES.map((p) => ({
      id: `nav:${p.href}`,
      label: `Config: ${p.label}`,
      group: "Go to" as const,
      keywords: p.keywords,
      href: p.href,
    })),
    ...OTHER_PAGES.map((p) => ({
      id: `nav:${p.href}`,
      label: p.label,
      group: "Go to" as const,
      keywords: p.keywords,
      href: p.href,
    })),
    ...DOMAIN_SLUGS.map((slug) => ({
      id: `nav:${historyHref(slug)}`,
      label: `History: ${CONFIG_DOMAINS[slug].label}`,
      group: "Go to" as const,
      keywords: "config versions diff rollback undo",
      href: historyHref(slug),
    })),
    { id: "act:sync", label: "Sync portfolio now", group: "Actions", keywords: "trading212 refresh", action: { kind: "portfolio-sync" } },
    { id: "act:news", label: "Check news now", group: "Actions", keywords: "ingest feeds run", action: { kind: "news-ingest" } },
    { id: "act:verdicts", label: "Re-run thesis verdicts", group: "Actions", keywords: "kpi judge", action: { kind: "thesis-verdicts" } },
    { id: "act:daily", label: "Write daily digest now", group: "Actions", keywords: "report send", action: { kind: "digest", period: "daily" } },
    { id: "act:weekly", label: "Write weekly digest now", group: "Actions", keywords: "report send", action: { kind: "digest", period: "weekly" } },
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
        id: `earnings:${h.ticker}`,
        label: `Re-run earnings check: ${name}`,
        group: "Actions" as const,
        keywords: `${h.ticker} earnings results extract verdict`,
        action: { kind: "earnings-check" as const, ticker: h.ticker },
      },
    ];
  });
}

export function filterCommands(commands: PaletteCommand[], query: string, limit = 12): PaletteCommand[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) {
    return commands.filter((c) => c.action?.kind !== "earnings-check").slice(0, limit);
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
