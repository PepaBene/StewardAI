"use client";

import { useCallback } from "react";

import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";

export type CompanyChecks = components["schemas"]["CompanyChecks"];
export type PortfolioScorecard = components["schemas"]["PortfolioScorecard"];

/** The company's cached checklist (`GET /companies/{ticker}/checks`). */
export function useCompanyChecks(ticker: string) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/checks", { params: { path: { ticker } } }), [ticker]);
  return useApiResource(fetcher);
}
