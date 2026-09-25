/** Top-level sections from the spec's information architecture. */
export const SECTIONS = [
  { slug: "", label: "Overview", description: "Portfolio value, allocation, drift and today's alerts at a glance." },
  { slug: "holdings", label: "Holdings", description: "One row per position, each opening to a holding page with its thesis, verdicts, news and evidence." },
  { slug: "news", label: "News", description: "Filterable feed by holding, severity, confidence and source tier." },
  { slug: "research", label: "Research", description: "Past research reports and a \"research this ticker\" action." },
  { slug: "reports", label: "Reports", description: "Digest archive and monthly reviews, searchable." },
  { slug: "chat", label: "Chat", description: "Persistent chat with the copilot." },
  { slug: "config", label: "Config", description: "Strategy, theses, sources, notifications, research profiles, schedules and budget." },
  { slug: "activity", label: "Activity", description: "Job log, coverage gaps and cost against budget." },
] as const;

export type Section = (typeof SECTIONS)[number];

/** Sections with a real page of their own; the rest render the [section] placeholder. */
export const BUILT_SLUGS: readonly string[] = ["", "holdings", "news", "reports", "config"];

export const PLACEHOLDER_SECTIONS = SECTIONS.filter((s) => !BUILT_SLUGS.includes(s.slug));

export function sectionHref(section: Pick<Section, "slug">) {
  return `/${section.slug}`;
}

/** Pages outside a section's own path that belong to it (highlighted as that section). */
export const SECTION_ALIASES: Record<string, readonly string[]> = {
  "/holdings": ["/transactions", "/theses", "/review"],
};

export function isSectionActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return [href, ...(SECTION_ALIASES[href] ?? [])].some((p) => pathname === p || pathname.startsWith(`${p}/`));
}
