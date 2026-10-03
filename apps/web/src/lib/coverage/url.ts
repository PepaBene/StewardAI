import type { EntityKind } from "@/lib/source-coverage/types";

import { ATLAS_KINDS, SUMMARY_GROUPS, type AtlasDrill, type AtlasFilters, type SummaryGroupBy } from "./types";

export const DEFAULT_ATLAS_FILTERS: AtlasFilters = {
  provider: "",
  dataset: "",
  kind: "any",
  state: "",
  period: "",
  entity: "",
  group: "metric",
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

type ParamsLike = { get(name: string): string | null };

function text(params: ParamsLike, name: string): string {
  return (params.get(name) ?? "").trim().slice(0, 128);
}

/** Reads the atlas filters from URL params; an invalid value falls back to its default. */
export function parseAtlasParams(params: ParamsLike): { filters: AtlasFilters; drill: AtlasDrill } {
  const kind = params.get("kind");
  const group = params.get("group");
  const period = text(params, "period");
  const filters: AtlasFilters = {
    provider: text(params, "provider"),
    dataset: text(params, "dataset"),
    kind: ATLAS_KINDS.includes(kind as EntityKind) ? (kind as EntityKind) : "any",
    state: text(params, "state"),
    period: ISO_DATE.test(period) && !Number.isNaN(Date.parse(period)) ? period : "",
    entity: text(params, "entity"),
    group: SUMMARY_GROUPS.includes(group as SummaryGroupBy) ? (group as SummaryGroupBy) : DEFAULT_ATLAS_FILTERS.group,
  };
  const drill = text(params, "drill");
  const at = drill.lastIndexOf("~");
  const drillKind = at > 0 ? drill.slice(at + 1) : "";
  return {
    filters,
    drill: at > 0 && ATLAS_KINDS.includes(drillKind as EntityKind) ? { dataset: drill.slice(0, at), kind: drillKind as EntityKind } : null,
  };
}

/** Serialises non-default filters (and the drill) to a query string without the leading "?". */
export function atlasQuery(filters: AtlasFilters, drill: AtlasDrill): string {
  const q = new URLSearchParams();
  if (filters.provider) q.set("provider", filters.provider);
  if (filters.dataset) q.set("dataset", filters.dataset);
  if (filters.kind !== "any") q.set("kind", filters.kind);
  if (filters.state) q.set("state", filters.state);
  if (filters.period) q.set("period", filters.period);
  if (filters.entity) q.set("entity", filters.entity);
  if (filters.group !== DEFAULT_ATLAS_FILTERS.group) q.set("group", filters.group);
  if (drill) q.set("drill", `${drill.dataset}~${drill.kind}`);
  return q.toString();
}
