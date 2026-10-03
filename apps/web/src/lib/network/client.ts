import { getBrowserApiClient } from "@/lib/api/client";
import type { ApiResult } from "@/lib/hooks/use-api-resource";

import {
  parseBusinessNetwork,
  parseConcentration,
  parseDeals,
  parseKnockOn,
  parseKpiLinks,
  parseObservations,
  parsePatentOverlap,
  parsePatentTrends,
  parseReconciliations,
  parseReviewQueue,
} from "./parse";
import type {
  BusinessNetwork,
  ConcentrationPayload,
  DealsPayload,
  KnockOnPayload,
  KpiLink,
  Parsed,
  PatentOverlapRow,
  PatentTrendRow,
  Reconciliation,
  ResearchObservation,
  ReviewQueue,
} from "./types";

/*
 * Read seams for the business-network view. Every call goes through the typed API client;
 * the untyped bodies are narrowed by the guards in parse.ts, so a resource's data is a
 * `Parsed<T>`: either the typed value or the reason the response was rejected.
 */

type Raw = { data?: unknown; error?: unknown; response: Response };

async function parsed<T>(call: Promise<Raw>, parse: (raw: unknown) => Parsed<T>): Promise<ApiResult<Parsed<T>>> {
  const r = await call;
  if (r.data === undefined) return { error: r.error, response: r.response };
  return { data: parse(r.data), response: r.response };
}

const api = () => getBrowserApiClient();
const tickerPath = (ticker: string) => ({ path: { ticker } });

export const fetchBusinessNetwork = (ticker: string, asOf: string | undefined) =>
  parsed<BusinessNetwork>(
    api().GET("/holdings/{ticker}/business-network", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }),
    parseBusinessNetwork,
  );

export const fetchDeals = (ticker: string, asOf: string | undefined) =>
  parsed<DealsPayload>(api().GET("/holdings/{ticker}/deals", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }), parseDeals);

export const fetchConcentration = (ticker: string, asOf: string | undefined) =>
  parsed<ConcentrationPayload>(
    api().GET("/holdings/{ticker}/customer-supplier-concentration", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }),
    parseConcentration,
  );

/** The patent-trends route has no `as_of`: it always reports the current attribution. */
export const fetchPatentTrends = (ticker: string) =>
  parsed<PatentTrendRow[]>(api().GET("/holdings/{ticker}/patent-trends", { params: tickerPath(ticker) }), parsePatentTrends);

export const fetchPatentOverlap = (ticker: string, asOf: string | undefined) =>
  parsed<PatentOverlapRow[]>(
    api().GET("/holdings/{ticker}/patent-competitor-overlap", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }),
    parsePatentOverlap,
  );

export const fetchKpiLinks = (ticker: string, asOf: string | undefined) =>
  parsed<KpiLink[]>(api().GET("/holdings/{ticker}/thesis-kpi-links", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }), parseKpiLinks);

export const fetchObservations = (ticker: string, asOf: string | undefined) =>
  parsed<ResearchObservation[]>(
    api().GET("/holdings/{ticker}/company-research-observations", { params: { ...tickerPath(ticker), query: { as_of: asOf } } }),
    parseObservations,
  );

export const fetchReconciliations = (ticker: string) =>
  parsed<Reconciliation[]>(api().GET("/company-graph/reconciliations", { params: { query: { ticker } } }), parseReconciliations);

export const fetchReviewQueue = (ticker: string) =>
  parsed<ReviewQueue>(api().GET("/relationship-review/queue", { params: { query: { ticker, limit: 100 } } }), parseReviewQueue);

export const fetchKnockOn = (asOf: string | undefined) =>
  parsed<KnockOnPayload>(api().GET("/portfolio/relationship-knock-on", { params: { query: { as_of: asOf } } }), parseKnockOn);

/* Typed reads (schemas exist in the OpenAPI document). */

export const fetchIdentity = (ticker: string) => api().GET("/holdings/{ticker}/identity", { params: tickerPath(ticker) });

export const fetchLegalEntityHistory = (lei: string, asOf: string | undefined) =>
  api().GET("/reference/legal-entities/{lei}/history", { params: { path: { lei }, query: { as_of: asOf } } });

export const fetchListingHistory = (isin: string, asOf: string | undefined) =>
  api().GET("/reference/listings/{isin}/history", { params: { path: { isin }, query: { as_of: asOf } } });

export const fetchMonthlyReviews = () => api().GET("/reviews", { params: { query: { limit: 24 } } });
