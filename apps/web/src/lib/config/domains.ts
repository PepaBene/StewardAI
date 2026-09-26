import type { ConfigDomainSlug } from "@/lib/api/client";

/*
 * Every versioned config domain, by the URL slug the API uses (/config/<slug>), with the
 * page that edits it (null while it has no editor). The export file names domains with
 * underscores (holding_profiles); `domainFromName` maps either spelling back.
 */
export const CONFIG_DOMAINS: Record<ConfigDomainSlug, { label: string; editor: string | null; note?: string }> = {
  strategy: { label: "Strategy", editor: "/config/strategy" },
  theses: { label: "Theses", editor: "/config/theses" },
  jobs: { label: "Jobs", editor: "/config/jobs" },
  "holding-profiles": { label: "Holding profiles", editor: "/config/holding-profiles" },
  verification: { label: "Verification", editor: "/config/verification" },
  "news-sources": { label: "News sources", editor: "/config/news-sources" },
  "event-rules": { label: "Event rules", editor: "/config/event-rules" },
  notifications: { label: "Notifications", editor: "/config/notifications" },
  "research-profiles": { label: "Research profiles", editor: "/config/research-profiles" },
  budget: { label: "Budget", editor: null, note: "Editor in phase 6" },
};

export const DOMAIN_SLUGS = Object.keys(CONFIG_DOMAINS) as ConfigDomainSlug[];

export function isDomainSlug(value: string): value is ConfigDomainSlug {
  return Object.hasOwn(CONFIG_DOMAINS, value);
}

/** "holding_profiles" or "holding-profiles" -> "holding-profiles"; unknown names -> null. */
export function domainFromName(name: string): ConfigDomainSlug | null {
  const slug = name.replaceAll("_", "-");
  return isDomainSlug(slug) ? slug : null;
}

export function domainLabel(name: string): string {
  const slug = domainFromName(name);
  return slug ? CONFIG_DOMAINS[slug].label : name;
}

export const historyHref = (slug: ConfigDomainSlug) => `/config/history/${slug}`;
