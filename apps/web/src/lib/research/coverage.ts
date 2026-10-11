import type { ResearchBody } from "@/lib/api/client";

/*
 * Search coverage, the plan and the stored-data tool calls of a research run. Every field
 * is optional in an older stored report, so each helper accepts missing values.
 */

export type DiscoveredUrl = NonNullable<ResearchBody["discovered_urls"]>[number];
export type SearchSummary = NonNullable<ResearchBody["search_summary"]>;
export type RoundRecord = NonNullable<ResearchBody["rounds"]>[number];
export type ResearchPlan = NonNullable<ResearchBody["plan"]>;
export type ToolCallRecord = NonNullable<ResearchBody["tool_calls"]>[number];
export type UrlStatus = DiscoveredUrl["status"];

export const URL_STATUSES: readonly { id: UrlStatus; label: string; description: string }[] = [
  { id: "cited", label: "Cited", description: "Used as a source in the report." },
  { id: "found_not_cited", label: "Found, not cited", description: "Returned by a search but no sentence rests on it." },
  { id: "excluded", label: "Excluded", description: "Left out by the source policy; the limit says why." },
];

export function urlStatusLabel(status: string): string {
  return URL_STATUSES.find((s) => s.id === status)?.label ?? status.replaceAll("_", " ");
}

export type UrlGroup = { status: UrlStatus; label: string; description: string; urls: DiscoveredUrl[] };

/** The discovered URLs grouped by status (cited first), each group by round then host. */
export function groupDiscoveredUrls(urls: readonly DiscoveredUrl[] | null | undefined): UrlGroup[] {
  const list = urls ?? [];
  return URL_STATUSES.map((s) => ({
    status: s.id,
    label: s.label,
    description: s.description,
    urls: list.filter((u) => u.status === s.id).sort((a, b) => a.round - b.round || a.host.localeCompare(b.host)),
  }));
}

/** What a URL could not give: the stored limitation, else its age when the provider gave one. */
export function urlLimit(url: Pick<DiscoveredUrl, "limitation" | "page_age">): string | null {
  if (url.limitation) return url.limitation;
  return url.page_age ? `Page age ${url.page_age}` : null;
}

/** Found, cited and excluded counts: from the stored summary, else counted from the URL list. Null when neither exists. */
export function coverageFigures(
  summary: SearchSummary | null | undefined,
  urls: readonly DiscoveredUrl[] | null | undefined,
): { found: number; cited: number; excluded: number } | null {
  if (summary) return { found: summary.urls_found, cited: summary.urls_cited, excluded: summary.urls_excluded };
  const list = urls ?? [];
  if (list.length === 0) return null;
  return {
    found: list.length,
    cited: list.filter((u) => u.status === "cited").length,
    excluded: list.filter((u) => u.status === "excluded").length,
  };
}

/** One line per follow-up round: what it re-searched and what it added. */
export function roundLine(round: RoundRecord, label: (section: string) => string): string {
  const sections = round.sections.length > 0 ? round.sections.map(label).join(", ") : "no section";
  const parts = [
    `${round.sentences_added} sentence${round.sentences_added === 1 ? "" : "s"}`,
    `${round.sources_added} source${round.sources_added === 1 ? "" : "s"} added`,
    `${round.urls_found} URL${round.urls_found === 1 ? "" : "s"} found`,
  ];
  return `Round ${round.round}: ${sections} (${parts.join(", ")})`;
}

/** The follow-up in words: sources after the first pass, what follow-up added, and why it stopped. */
export function followUpSummary(
  summary: SearchSummary | null | undefined,
  rounds: readonly RoundRecord[] | null | undefined,
  stoppedBecause: string | null | undefined,
): string[] {
  const out: string[] = [];
  if (summary) {
    out.push(
      `${summary.sources_after_first_pass} source${summary.sources_after_first_pass === 1 ? "" : "s"} after the first pass; follow-up added ${summary.sources_added_by_follow_up}.`,
    );
  }
  const n = rounds?.length ?? 0;
  if (n > 0) out.push(`${n} follow-up round${n === 1 ? "" : "s"}.`);
  if (stoppedBecause) out.push(`Stopped because: ${stoppedBecause}`);
  return out;
}

// ---------------------------------------------------------------------------
// Plan
// ---------------------------------------------------------------------------

/** The plan's stop rule as separate limits plus the rule in words. */
export function stopRuleLines(stop: ResearchPlan["stop"] | null | undefined): string[] {
  if (!stop) return [];
  return [
    `Up to ${stop.max_steps} steps, ${stop.max_searches} searches and ${stop.max_rounds} follow-up round${stop.max_rounds === 1 ? "" : "s"}.`,
    `A section counts as answered with ${stop.min_sentences} cited sentence${stop.min_sentences === 1 ? "" : "s"}.`,
    ...(stop.rule ? [stop.rule] : []),
  ];
}

// ---------------------------------------------------------------------------
// Stored-data tool calls
// ---------------------------------------------------------------------------

export const TOOL_STATUS_LABELS: Record<ToolCallRecord["status"], string> = {
  ok: "Fresh",
  stale: "Stale",
  missing: "No data",
  failed: "Failed",
  skipped: "Skipped",
};

export function toolStatusLabel(status: string): string {
  return TOOL_STATUS_LABELS[status as ToolCallRecord["status"]] ?? status;
}

/** "get_holding" -> "Get holding". */
export function toolLabel(tool: string): string {
  const text = tool.replaceAll("_", " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Why a call did not give fresh data: the skip note or the error; null for a call that did. */
export function toolReason(call: Pick<ToolCallRecord, "status" | "note" | "error">): string | null {
  if (call.status === "ok") return null;
  return call.error || call.note || null;
}

/** Figures by status ("12 ok, 2 stale") and the newest as-of date. */
export function toolFreshness(call: Pick<ToolCallRecord, "figures" | "as_of">): string | null {
  const figures = Object.entries(call.figures ?? {}).filter(([, n]) => n > 0);
  const parts = figures.map(([status, n]) => `${n} ${status.replaceAll("_", " ")}`);
  if (call.as_of) parts.push(`newest as of ${call.as_of}`);
  return parts.length > 0 ? parts.join(", ") : null;
}

/** Calls per status, in a fixed order, omitting statuses with none. */
export function toolCallCounts(calls: readonly Pick<ToolCallRecord, "status">[] | null | undefined): { status: string; label: string; count: number }[] {
  return (Object.keys(TOOL_STATUS_LABELS) as ToolCallRecord["status"][])
    .map((status) => ({ status, label: TOOL_STATUS_LABELS[status], count: (calls ?? []).filter((c) => c.status === status).length }))
    .filter((c) => c.count > 0);
}
