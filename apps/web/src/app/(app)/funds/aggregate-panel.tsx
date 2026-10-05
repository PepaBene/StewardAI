"use client";

import { useMemo, useState } from "react";

import { HBar, StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { bucketSegments, bucketTotal, parseAggregate } from "@/lib/funds/funds";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";

const fetchAggregate = () => getBrowserApiClient().GET("/funds/aggregate", { params: { query: {} } });

const UNKNOWN = <span className="text-text-muted">Unknown</span>;

/** Direct stocks plus fund holdings merged by issuer. Every share is of total portfolio value. */
export function AggregatePanel() {
  const { state } = useApiResource(fetchAggregate);
  const data = dataOf(state);
  const agg = useMemo(() => (data ? parseAggregate(data) : null), [data]);
  const [dimension, setDimension] = useState<string | null>(null);
  const view = agg ? (agg.exposures.find((e) => e.dimension === dimension) ?? agg.exposures[0] ?? null) : null;
  const maxIssuer = agg ? Math.max(0, ...agg.issuers.map((i) => i.weight ?? 0)) : 0;

  return (
    <Panel
      id="aggregate"
      title="Combined exposure"
      description="Direct shares plus each fund's weight times its published rows, merged by issuer. Every share below is of total portfolio value. Weight the rows do not account for is its own segment and is never spread over the rest."
    >
      <ResourceNotice state={state} what="combined exposure" />
      {agg && !agg.available ? (
        <EmptyState title="No fund is looked through yet">Combined exposure appears once at least one fund has collected holdings.</EmptyState>
      ) : null}
      {agg && agg.available ? (
        <div className="space-y-5">
          {view ? (
            <div className="space-y-2">
              <div role="group" aria-label="Exposure dimension" className="flex flex-wrap gap-1.5">
                {agg.exposures.map((e) => (
                  <Button
                    key={e.dimension}
                    size="sm"
                    variant={e.dimension === view.dimension ? "default" : "outline"}
                    aria-pressed={e.dimension === view.dimension}
                    onClick={() => setDimension(e.dimension)}
                  >
                    {titleCase(e.dimension)}
                  </Button>
                ))}
              </div>
              <StackedBar
                title={`${titleCase(view.dimension)}, share of total portfolio value`}
                note={view.reconciles === false ? "Buckets do not add up to the total" : undefined}
                segments={bucketSegments(view)}
              />
              <p className="text-xs text-text-muted">
                Known segments add up to {formatPct(bucketTotal(view), 1)} of total portfolio value
                {agg.unknownFundWeight === null ? "." : `; ${formatPct(agg.unknownFundWeight, 1)} sits in funds whose rows do not account for it.`}
                {agg.notLookedThroughWeight !== null ? ` ${formatPct(agg.notLookedThroughWeight, 1)} is in funds not looked through.` : ""}
              </p>
            </div>
          ) : null}

          {agg.issuers.length > 0 ? (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Largest issuers, share of total portfolio value</h3>
              <ul className="divide-y divide-border-soft border border-border">
                {agg.issuers.slice(0, 15).map((i) => (
                  <li key={i.key} className="grid grid-cols-1 items-center gap-x-4 gap-y-1 px-3 py-1.5 sm:grid-cols-[minmax(10rem,16rem)_1fr_10rem]">
                    <span className="truncate text-sm">{i.label}</span>
                    <HBar value={i.weight} max={maxIssuer || 1} label={`${i.label} share of total portfolio value`} formatValue={(v) => formatPct(v, 2)} />
                    <span className="num text-right text-xs text-text-muted">
                      {i.overlapWeight !== null && i.overlapWeight > 0 ? `${formatPct(i.overlapWeight, 2)} reached twice` : i.channels > 1 ? "via several channels" : "single channel"}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <caption className="sr-only">Funds in the combined exposure</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Fund</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Of portfolio</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Unknown, of portfolio</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Fee per year</th>
                  <th scope="col" className="py-1.5 font-medium">Holdings</th>
                </tr>
              </thead>
              <tbody>
                {agg.funds.map((f) => (
                  <tr key={f.isin || f.label} className="border-b border-border-soft">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">{f.label}</th>
                    <td className="num py-1.5 pr-3 text-right">{f.weight === null ? UNKNOWN : formatPct(f.weight, 1)}</td>
                    <td className="num py-1.5 pr-3 text-right">{f.unknownWeight === null ? UNKNOWN : formatPct(f.unknownWeight, 1)}</td>
                    <td className="num py-1.5 pr-3 text-right">{f.expenseRatio === null ? <span className="text-text-muted">Not published</span> : formatPct(f.expenseRatio, 2)}</td>
                    <td className="py-1.5 text-xs text-text-muted">
                      {titleCase(f.status)}
                      {f.holdingsAsOf ? ` · as of ${f.holdingsAsOf}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-text-muted">
            {agg.annualFeeDrag === null
              ? "Fee drag is not computable."
              : `Known fund fees cost ${formatPct(agg.annualFeeDrag, 2)} of total portfolio value per year`}
            {agg.annualFeeDrag !== null && agg.unpricedFeeWeight !== null && agg.unpricedFeeWeight > 0
              ? `, with ${formatPct(agg.unpricedFeeWeight, 1)} in funds whose fee is unknown (not assumed 0).`
              : agg.annualFeeDrag !== null
                ? "."
                : ""}
          </p>
        </div>
      ) : null}
    </Panel>
  );
}
