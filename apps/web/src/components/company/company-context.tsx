"use client";

import { createContext, useContext } from "react";

import type { CompanyChecks } from "@/lib/insight/checks";
import type { Overview } from "@/lib/company/company";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/*
 * What the company shell fetches once and shares: the overview (name, price, listings), the cached
 * checklist (score mark, "n of 6 checks") and the fair value the Valuation section computed. The
 * sidebar, the compact header and every section read these, so none of them asks again.
 */

const OverviewContext = createContext<ResourceState<Overview>>({ kind: "loading" });
const ChecksContext = createContext<ResourceState<CompanyChecks>>({ kind: "loading" });

export const OverviewProvider = OverviewContext.Provider;
export const ChecksProvider = ChecksContext.Provider;

/** The company's overview, fetched once by the shell and shared by the header and the Overview tab. */
export function useCompanyOverview(): ResourceState<Overview> {
  return useContext(OverviewContext);
}

/** The company's checklist, fetched once by the shell. */
export function useShellChecks(): ResourceState<CompanyChecks> {
  return useContext(ChecksContext);
}

/** One fair value with the basis it rests on, shown as a chip in the sidebar. */
export type FairValueReport = { value: number; currency: string | null; basis: string; price: number | null };

type FairValueSlot = { report: FairValueReport | null; setReport: (r: FairValueReport | null) => void };
const FairValueContext = createContext<FairValueSlot>({ report: null, setReport: () => undefined });

export const FairValueProvider = FairValueContext.Provider;

export function useFairValueReport(): FairValueSlot {
  return useContext(FairValueContext);
}
