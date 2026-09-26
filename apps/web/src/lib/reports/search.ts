import type { ReportHit, ReportType } from "@/lib/api/client";

/*
 * The report archive's full-text search (`GET /reports/search?q=&type=`): every digest,
 * monthly review, research report and impact note containing the words, a ticker or a
 * "quoted phrase", newest first. The API marks matches in the excerpt with <b>…</b>; those
 * markers are parsed here into plain segments, so the page highlights them without
 * inserting HTML.
 */

export const REPORT_TYPES = ["digest", "review", "research", "escalation"] as const satisfies readonly ReportType[];

export const REPORT_TYPE_LABELS: Record<ReportType, { label: string; plural: string }> = {
  digest: { label: "Digest", plural: "Digests" },
  review: { label: "Monthly review", plural: "Monthly reviews" },
  research: { label: "Research", plural: "Research" },
  escalation: { label: "Impact note", plural: "Impact notes" },
};

export const QUERY_MAX = 200;

/** The page a hit opens, by report type. */
export function reportHitHref(hit: Pick<ReportHit, "id" | "type">): string {
  switch (hit.type) {
    case "digest":
      return `/reports/${hit.id}`;
    case "research":
      return `/research/${hit.id}`;
    case "review":
      return `/reviews/${hit.id}`;
    case "escalation":
      return `/escalations/${hit.id}`;
  }
}

export type Segment = { text: string; highlight: boolean };

/**
 * "a <b>AAPL</b> b" -> [{a }, {AAPL, highlight}, { b}]. Only <b> and </b> are markers
 * (any case); every other character, including other tags, stays literal text. An
 * unclosed <b> highlights to the end; a stray </b> is dropped. Adjacent segments of the
 * same kind are merged and empty ones removed.
 */
export function parseHighlights(excerpt: string): Segment[] {
  const out: Segment[] = [];
  const push = (text: string, highlight: boolean) => {
    if (!text) return;
    const last = out[out.length - 1];
    if (last && last.highlight === highlight) last.text += text;
    else out.push({ text, highlight });
  };
  const marker = /<(\/?)b>/gi;
  let at = 0;
  let open = false;
  for (let m = marker.exec(excerpt); m; m = marker.exec(excerpt)) {
    push(excerpt.slice(at, m.index), open);
    at = m.index + m[0].length;
    open = m[1] !== "/";
  }
  push(excerpt.slice(at), open);
  return out;
}

/** The excerpt as plain text (markers removed), e.g. for a title attribute. */
export function plainExcerpt(excerpt: string): string {
  return parseHighlights(excerpt)
    .map((s) => s.text)
    .join("");
}

export type SearchState = { q: string; types: ReportType[] };

/** Read `?q=` and repeated `?type=` from the URL; unknown types are ignored. */
export function searchFromParams(params: Pick<URLSearchParams, "get" | "getAll">): SearchState {
  const q = (params.get("q") ?? "").trim().slice(0, QUERY_MAX);
  const types = [...new Set(params.getAll("type"))].filter((t): t is ReportType => (REPORT_TYPES as readonly string[]).includes(t));
  return { q, types };
}

/** "/reports?q=AAPL&type=research&type=review" (types in a fixed order). */
export function searchHref(state: SearchState, base = "/reports"): string {
  const params = new URLSearchParams();
  const q = state.q.trim();
  if (q) params.set("q", q);
  for (const t of REPORT_TYPES) if (state.types.includes(t)) params.append("type", t);
  const query = params.toString();
  return query ? `${base}?${query}` : base;
}

/** Newest first, as the API returns them; re-sorted defensively (then by id). */
export function sortHits(hits: readonly ReportHit[]): ReportHit[] {
  return [...hits].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
}
