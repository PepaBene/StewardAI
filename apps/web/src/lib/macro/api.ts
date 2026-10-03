import { getBrowserApiClient, type HoldingsResponse } from "@/lib/api/client";
import type { ApiResult } from "@/lib/hooks/use-api-resource";

import {
  parseExposureMappings,
  parseMacroContext,
  parsePortfolioCountryContext,
  parsePortfolioMacroCoverage,
  parseResearchContext,
  parseTradeContext,
  type Parsed,
} from "./parse";
import type {
  ExposureMapping,
  HoldingMacroContext,
  PortfolioCountryContext,
  PortfolioMacroCoverage,
  ResearchContext,
  TradeContext,
} from "./types";

/*
 * Fetchers for the macro views. Each calls the typed client and runs the body through its
 * parser: a body of an unexpected shape becomes an error result (HTTP 502, with the reason) so
 * `useApiResource` shows "unavailable" and keeps the last good value, instead of a blank view.
 */

const api = () => getBrowserApiClient();

async function parsed<TIn, TOut>(
  call: Promise<{ data?: TIn; error?: unknown; response: Response }>,
  parse: (body: unknown) => Parsed<TOut>,
): Promise<ApiResult<TOut>> {
  const result = await call;
  if (!result.response.ok || result.data === undefined) return { error: result.error, response: result.response };
  const p = parse(result.data);
  if (p.ok) return { data: p.value, response: result.response };
  return { error: { detail: `Unexpected response shape: ${p.reason}` }, response: new Response(null, { status: 502 }) };
}

export const fetchHoldings = (): Promise<ApiResult<HoldingsResponse>> => api().GET("/portfolio/holdings", {});

export const fetchPortfolioCountryContext = (): Promise<ApiResult<PortfolioCountryContext>> =>
  parsed(api().GET("/portfolio/data-context", { params: { query: {} } }), parsePortfolioCountryContext);

export const fetchPortfolioMacroCoverage = (): Promise<ApiResult<PortfolioMacroCoverage>> =>
  parsed(api().GET("/macro/coverage/portfolio", {}), parsePortfolioMacroCoverage);

export const fetchExposureMappings = (): Promise<ApiResult<ExposureMapping[]>> => parsed(api().GET("/macro/exposures", {}), parseExposureMappings);

/** History periods requested for the vintage small multiples (backend maximum). */
export const VINTAGE_HISTORY_PERIODS = 60;

/** `asOf`: an ISO date-time; omit for now. The vintage history is requested with every read. */
export const fetchMacroContext = (ticker: string, asOf: string | null): Promise<ApiResult<HoldingMacroContext>> =>
  parsed(
    api().GET("/macro/context/{ticker}", { params: { path: { ticker }, query: { history: VINTAGE_HISTORY_PERIODS, ...(asOf ? { as_of: asOf } : {}) } } }),
    parseMacroContext,
  );

const TRADE_HISTORY_PERIODS = 60;

export const fetchTradeContext = (reporter: string, hsCode: string): Promise<ApiResult<TradeContext>> =>
  parsed(api().GET("/macro/trade/{reporter}/{hs_code}", { params: { path: { reporter, hs_code: hsCode }, query: { history_periods: TRADE_HISTORY_PERIODS } } }), parseTradeContext);

export const fetchCountryContext = (geography: string): Promise<ApiResult<ResearchContext>> =>
  parsed(api().GET("/research-context/country/{geography}", { params: { path: { geography }, query: {} } }), parseResearchContext);

export const fetchIndustryContext = (hsCode: string): Promise<ApiResult<ResearchContext>> =>
  parsed(api().GET("/research-context/industry/{hs_code}", { params: { path: { hs_code: hsCode }, query: {} } }), parseResearchContext);

export function createExposure(body: import("./logic").ExposureBody) {
  return api().POST("/macro/exposures", { body });
}

export function deleteExposure(id: number) {
  return api().DELETE("/macro/exposures/{mapping_id}", { params: { path: { mapping_id: id } } });
}

export function runMacroCollect() {
  return api().POST("/jobs/macro-collect", {});
}

export type IssuerInfo = { id: number; name: string; lei: string | null; cik: string | null; country: string | null; tickers: string[] };

/** Legal names (with LEI, CIK, country and held tickers) for issuer ids, from GET /macro/issuers. */
export async function fetchIssuerNames(ids: readonly number[]): Promise<ApiResult<IssuerInfo[]>> {
  if (ids.length === 0) return { data: [], response: new Response(null, { status: 200 }) };
  const result = await api().GET("/macro/issuers", { params: { query: { ids: [...ids], limit: 500 } } });
  if (!result.response.ok || result.data === undefined) return { error: result.error, response: result.response };
  return {
    data: result.data.map((i) => ({ id: i.id, name: i.name, lei: i.lei, cik: i.cik, country: i.country, tickers: i.tickers })),
    response: result.response,
  };
}

/** The names an issuer id carries today (legal name and aliases), to show which company an id belongs to. */
export async function fetchIssuerName(issuerId: number): Promise<ApiResult<{ issuerId: number; names: string[] }>> {
  const result = await api().GET("/reference/issuers/{issuer_id}/as-of", { params: { path: { issuer_id: issuerId }, query: { date: new Date().toISOString().slice(0, 10) } } });
  if (!result.response.ok || result.data === undefined) return { error: result.error, response: result.response };
  const names = [result.data.legal_name, ...result.data.aliases.map((a) => a.name)].filter((n): n is string => !!n);
  return { data: { issuerId, names: [...new Set(names)] }, response: result.response };
}
