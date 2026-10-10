"use client";

import { useMemo } from "react";

import { DataAgeBadge, ageInDays } from "@/components/freshness/data-age-badge";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { asOfQuery } from "@/lib/xray/as-of";

/** How old the holdings under every X-Ray tab are: the daily snapshot and the oldest published
 * fund holdings that were opened up. Shows nothing while loading or when the load fails. */
export function DataAgeStrip({ asOf }: { asOf?: string }) {
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/xray", { params: { query: asOfQuery(asOf) } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  if (state.kind !== "ok") return null;
  const data = state.data;
  const against = asOf ? new Date(`${asOf}T00:00:00Z`) : undefined;
  const fundDates = data.funds
    .filter((f) => f.status === "ok" || f.status === "stale")
    .map((f) => f.holdings_as_of)
    .filter((d): d is string => !!d)
    .sort();
  const oldestFund = fundDates[0];
  return (
    <DataAgeBadge
      items={[
        { label: "Holdings snapshot", date: data.snapshot_date, ageDays: ageInDays(data.snapshot_date, against) },
        ...(oldestFund ? [{ label: "Oldest fund holdings", date: oldestFund, ageDays: ageInDays(oldestFund, against) }] : []),
      ]}
    />
  );
}
