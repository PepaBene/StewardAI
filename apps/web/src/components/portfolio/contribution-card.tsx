"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ContributionWaterfall } from "@/components/portfolio/contribution-waterfall";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { Button } from "@/components/ui/button";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type ContributionReport } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { DEFAULT_RANGE, PRICE_RANGES, rangeDays, type PriceRangeId } from "@/lib/reference/prices";

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
    <Blueprint className="flex min-w-0 flex-col gap-4 px-6 py-5.5" aria-label="Contribution to return">
      <PanelHeading
        kicker="Contribution"
        title="Why the portfolio moved"
        action={
          <RangeButtons
            options={PRICE_RANGES.map((r) => ({ id: r.id, label: r.label }))}
            value={range}
            onChange={setRange}
            label="Contribution period"
          />
        }
      />
      <p className="-mt-2 text-xs text-text-muted">Each holding&rsquo;s contribution to the return, its own trading netted out.</p>
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
    </Blueprint>
  );
}

export function ContributionContent({ data }: { data: ContributionReport }) {
  // One snapshot has no period: the API answers with a zero for every holding and no gap. Those
  // zeros are "nothing to attribute yet", not "no holding contributed"; they are never drawn.
  if (!data.gap && data.holdings.length > 0 && data.period_start && data.as_of && data.period_start >= data.as_of) {
    return (
      <p className="text-sm text-text-muted">
        Only one snapshot exists (as of {data.as_of}), so there is no period to attribute yet. Contributions appear after the next sync.
      </p>
    );
  }
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
