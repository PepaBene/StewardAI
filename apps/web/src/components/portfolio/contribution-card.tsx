"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ContributionWaterfall } from "@/components/portfolio/contribution-waterfall";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBrowserApiClient, type ContributionReport } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { DEFAULT_RANGE, PRICE_RANGES, rangeDays, type PriceRangeId } from "@/lib/reference/prices";
import { cn } from "@/lib/utils";

/**
 * Per-holding contribution to the period's return (§5.4), self-fetching its own window
 * like PerformanceCard: GET /portfolio/contribution, a new endpoint (steward.portfolio.
 * read.contribution_to_return) built on the same position_snapshot rows the holdings table
 * already reads daily, with each holding's own trading netted out.
 */
export function ContributionCard() {
  const [range, setRange] = useState<PriceRangeId>(DEFAULT_RANGE);
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/portfolio/contribution", { params: { query: { days: rangeDays(range) } } }),
    [range],
  );
  const { state, reload } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div>
          <CardTitle>Contribution to return</CardTitle>
          <CardDescription>Why the portfolio moved: each holding&rsquo;s own trading netted out.</CardDescription>
        </div>
        <RangeSelector value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {state.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading contribution…
          </p>
        ) : null}
        {state.kind === "error" ? (
          <AlertMessage title={state.title}>
            <p>{state.detail}</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
              <RefreshCwIcon aria-hidden="true" />
              Try again
            </Button>
          </AlertMessage>
        ) : null}
        {data ? <ContributionContent data={data} /> : null}
      </CardContent>
    </Card>
  );
}

function RangeSelector({ value, onChange }: { value: PriceRangeId; onChange: (id: PriceRangeId) => void }) {
  return (
    <div role="group" aria-label="Contribution period" className="inline-flex rounded-md border border-border bg-surface-raised p-0.5">
      {PRICE_RANGES.map((r) => (
        <button
          key={r.id}
          type="button"
          aria-pressed={value === r.id}
          onClick={() => onChange(r.id)}
          className={cn(
            "rounded-sm px-2.5 py-1 text-xs font-medium",
            value === r.id ? "bg-accent text-accent-foreground" : "text-text-muted hover:bg-surface-muted hover:text-text",
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function ContributionContent({ data }: { data: ContributionReport }) {
  if (data.gap || data.holdings.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        {data.gap === "no position snapshot yet"
          ? "No portfolio snapshot yet. The first Trading212 sync creates one."
          : (data.gap ?? "No holdings with enough history in this range yet.")}
      </p>
    );
  }
  return (
    <ContributionWaterfall
      holdings={data.holdings}
      currency={data.base_currency ?? ""}
      label="Per-holding contribution to the portfolio's return this period"
    />
  );
}
