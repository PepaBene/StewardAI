/* Groups a report's sources by website, for the sources rail. */

export type DomainGroup<T> = { domain: string; items: { item: T; index: number }[] };

/** "https://www.reuters.com/a/b" becomes "reuters.com"; text that is not a URL stays as written. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "") || url;
  } catch {
    return url.trim() || "Unknown source";
  }
}

/**
 * Groups items by domain, the most cited domain first (ties by name). Each item keeps its
 * original position, because citations in the report point to that number.
 */
export function groupByDomain<T>(items: readonly T[], urlOf: (item: T) => string): DomainGroup<T>[] {
  const groups = new Map<string, DomainGroup<T>>();
  items.forEach((item, index) => {
    const domain = domainOf(urlOf(item));
    const g = groups.get(domain) ?? { domain, items: [] };
    g.items.push({ item, index });
    groups.set(domain, g);
  });
  return [...groups.values()].sort((a, b) => b.items.length - a.items.length || a.domain.localeCompare(b.domain));
}
