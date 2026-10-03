"use client";

import { useCallback, useMemo } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { mapResource } from "@/lib/source-coverage/hooks";

import { adaptDataContext, adaptMeasuredCoverage, adaptSummary, type DataContextFigures } from "./parse";
import type { AtlasFilters, CoverageSummaryData, SummaryGroupBy } from "./types";

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

/** The staleness limit the scorecard judges a held close against (sessions); the plan's default. */
export const PRICE_MAX_STALE_SESSIONS = 3;

const fetchPriceCoverage = () => getBrowserApiClient().GET("/portfolio/price-coverage", { params: { query: { max_stale_sessions: PRICE_MAX_STALE_SESSIONS } } });

/** `GET /portfolio/price-coverage`: value-weighted held-price coverage, the backend's own figures. */
export function usePriceCoverage() {
  return useApiResource(fetchPriceCoverage);
}

const fetchDataReach = () => getBrowserApiClient().GET("/data/reach");

/** `GET /data/reach`: stored coverage by exchange, country, asset class and metric. */
export function useDataReach() {
  return useApiResource(fetchDataReach);
}

const fetchProvenance = () => getBrowserApiClient().GET("/data/provenance-completeness");

/** `GET /data/provenance-completeness`: share of rows per fact table carrying each provenance dimension. */
export function useProvenanceCompleteness() {
  return useApiResource(fetchProvenance);
}

const fetchIdentityReview = () => getBrowserApiClient().GET("/portfolio/identity-review");

/** `GET /portfolio/identity-review`: held positions with a reviewed identity path, and the unresolved ones. */
export function useIdentityReview() {
  return useApiResource(fetchIdentityReview);
}

const fetchMeasured = () => getBrowserApiClient().GET("/verification/coverage");

/** `GET /verification/coverage` (typed): measured rows by jurisdiction and metric. */
export function useMeasuredCoverage() {
  const { state: raw, reload } = useApiResource(fetchMeasured);
  const result = useMemo(() => mapResource(raw, adaptMeasuredCoverage), [raw]);
  return { state: result, reload };
}
