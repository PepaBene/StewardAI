"use client";

import { useCallback, useMemo } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { mapResource } from "@/lib/source-coverage/hooks";

import { adaptDataContext, adaptSummary, parseMeasuredCoverage, type DataContextFigures } from "./parse";
import type { AtlasFilters, CoverageSummaryData, MeasuredCoverageRow, SummaryGroupBy } from "./types";
import type { HeldPriceCount } from "./logic";

/** Atlas filters as the query the backend understands (empty = no filter). */
export function coverageQuery(f: Pick<AtlasFilters, "provider" | "dataset" | "state" | "period" | "entity">) {
  return {
    provider: f.provider || undefined,
    dataset: f.dataset || undefined,
    state: f.state || undefined,
    period: f.period || undefined,
    entity_key: f.entity || undefined,
  };
}

/** `GET /data/coverage/summary?group_by=...` with the atlas filters applied by the backend. */
export function useCoverageSummary(groupBy: SummaryGroupBy, filters?: Pick<AtlasFilters, "provider" | "dataset" | "state" | "period" | "entity" | "kind">) {
  const kind = filters?.kind ?? "any";
  const provider = filters?.provider ?? "";
  const dataset = filters?.dataset ?? "";
  const state = filters?.state ?? "";
  const period = filters?.period ?? "";
  const entity = filters?.entity ?? "";
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/data/coverage/summary", {
        params: { query: { group_by: groupBy, ...coverageQuery({ provider, dataset, state, period, entity }), entity_kind: kind === "any" ? undefined : kind } },
      }),
    [groupBy, provider, dataset, state, period, entity, kind],
  );
  const { state: raw, reload } = useApiResource(fetcher);
  const result = useMemo(() => mapResource(raw, (b): CoverageSummaryData => adaptSummary(b)), [raw]);
  return { state: result, reload };
}

const fetchDataContext = () => getBrowserApiClient().GET("/portfolio/data-context");

/** `GET /portfolio/data-context`: identity and return-window coverage with denominators. */
export function useDataContext() {
  const { state: raw, reload } = useApiResource(fetchDataContext);
  const result = useMemo(() => mapResource(raw, (b): DataContextFigures => adaptDataContext(b)), [raw]);
  return { state: result, reload };
}

const fetchGaps = () => getBrowserApiClient().GET("/reference/gaps");

/** Number of holdings the reference-data gap list names as unresolved identity exceptions. */
export function useIdentityGaps() {
  const { state: raw } = useApiResource(fetchGaps);
  return useMemo(() => mapResource(raw, (b) => b.identity.length), [raw]);
}

const fetchHeldPrices = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 7 } } });

/** Held positions with a stored latest close, counted from `GET /portfolio/holdings/prices`. */
export function useHeldPriceCount() {
  const { state: raw } = useApiResource(fetchHeldPrices);
  return useMemo(
    () =>
      mapResource(raw, (b): HeldPriceCount => {
        const asOfs = b.holdings.map((h) => h.as_of).filter((d): d is string => typeof d === "string");
        return {
          withClose: b.holdings.filter((h) => h.last_close !== null && h.as_of !== null).length,
          total: b.holdings.length,
          newestAsOf: asOfs.length ? asOfs.reduce((a, c) => (a > c ? a : c)) : null,
        };
      }),
    [raw],
  );
}

const fetchMeasured = () => getBrowserApiClient().GET("/verification/coverage");

/** `GET /verification/coverage`, parsed; a malformed body is an error, never an empty table. */
export function useMeasuredCoverage() {
  const { state: raw, reload } = useApiResource(fetchMeasured);
  const result = useMemo((): ResourceState<MeasuredCoverageRow[]> => {
    const bad = { kind: "error" as const, title: "Unexpected measured-coverage response", detail: "The body did not match the expected rows, so nothing is shown rather than a guess.", previous: null };
    if (raw.kind === "ok") {
      const rows = parseMeasuredCoverage(raw.data);
      return rows ? { kind: "ok", data: rows } : bad;
    }
    if (raw.kind === "error") return { ...raw, previous: raw.previous === null ? null : parseMeasuredCoverage(raw.previous) };
    return raw;
  }, [raw]);
  return { state: result, reload };
}
