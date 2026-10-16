import type { MarkAxis } from "@/lib/insight/insight";

/*
 * The nine company sections (redesign plan §5.3). Each section is a URL sub-route; a section
 * with several routes shows a small secondary sub-nav. The first route of a section is its home.
 * `axis` ties a section to one of the five check axes ("n of 6 checks"); Overview, Ownership,
 * Research and Network have no axis of their own.
 */

export type CompanySectionId =
  | "overview"
  | "valuation"
  | "future"
  | "past"
  | "health"
  | "dividend"
  | "ownership"
  | "research"
  | "network";

export type SectionRoute = { slug: string; label: string };

export type CompanySection = {
  id: CompanySectionId;
  number: number;
  label: string;
  axis: MarkAxis | null;
  routes: readonly SectionRoute[];
};

export const COMPANY_SECTIONS: readonly CompanySection[] = [
  { id: "overview", number: 1, label: "Overview", axis: null, routes: [{ slug: "", label: "Overview" }] },
  {
    id: "valuation",
    number: 2,
    label: "Valuation",
    axis: "value",
    routes: [
      { slug: "valuation", label: "Valuation" },
      { slug: "consensus", label: "Analyst targets" },
      { slug: "peers", label: "Peers" },
    ],
  },
  { id: "future", number: 3, label: "Future", axis: "future", routes: [{ slug: "estimates", label: "Estimates" }] },
  {
    id: "past",
    number: 4,
    label: "Past",
    axis: "past",
    routes: [
      { slug: "financials", label: "Financials" },
      { slug: "segments", label: "Segments" },
    ],
  },
  {
    id: "health",
    number: 5,
    label: "Health",
    axis: "health",
    routes: [
      { slug: "ratios", label: "Ratios" },
      { slug: "allocation", label: "Capital allocation" },
    ],
  },
  { id: "dividend", number: 6, label: "Dividend", axis: "dividend", routes: [{ slug: "dividends", label: "Dividends" }] },
  { id: "ownership", number: 7, label: "Ownership and management", axis: null, routes: [{ slug: "governance", label: "Governance" }] },
  {
    id: "research",
    number: 8,
    label: "Research and thesis",
    axis: null,
    routes: [
      { slug: "case", label: "Investment case" },
      { slug: "assessment", label: "Assessment" },
      { slug: "report", label: "Report" },
    ],
  },
  { id: "network", number: 9, label: "Network and reference", axis: null, routes: [{ slug: "network", label: "Network and patents" }] },
];

/** The full check list keeps its own route; it belongs to no section. */
export const CHECKS_SLUG = "checks";

/** The section that owns the first path segment after `/companies/[ticker]`, or null (checks, unknown). */
export function sectionForSlug(slug: string): CompanySection | null {
  return COMPANY_SECTIONS.find((s) => s.routes.some((r) => r.slug === slug)) ?? null;
}

/** The path segment after `/companies/[ticker]/`, "" for the overview. */
export function slugFromPath(pathname: string): string {
  const m = /^\/companies\/[^/]+\/?([^/?#]*)/.exec(pathname);
  return m ? decodeURIComponent(m[1]) : "";
}

/** True on the first route of a section: where the holding-page panels for that section are shown. */
export function isSectionHome(section: CompanySection, slug: string): boolean {
  return section.routes[0].slug === slug;
}
