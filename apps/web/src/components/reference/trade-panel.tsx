"use client";

import { useCallback, useState } from "react";

import { Sankey } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { BASIS_LABEL } from "@/lib/macro/context";
import { availableFlows, FLOW_LABEL, topPartnerShare, tradeDiagram, type TradeExposure, type TradeFlow } from "@/lib/macro/trade";
import { cn } from "@/lib/utils";

/**
 * Trade in the products you mapped this holding to, from UN Comtrade: how the country's exports
 * or imports of that product split by partner. These are national statistics. They never show who
 * the company sells to or buys from.
 */
export function TradePanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/data-context", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const exposures = data?.trade_exposure ?? [];

  return (
    <Blueprint aria-labelledby="trade-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Trade · UN Comtrade" title={<span id="trade-heading">Trade in the products it depends on</span>} />
      {state.kind === "loading" && !data ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !data ? (
        <p className="text-sm text-text-muted">
          Trade data unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {data && exposures.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No product is mapped to this holding, so no trade data is attached. Map a segment to a product (HS) code and a
          country in the exposure editor. Trade rows never identify a company&apos;s customers or suppliers.
        </p>
      ) : null}

      {exposures.map((x) => (
        <ExposureBlock key={`${x.geography ?? ""}|${x.hs_code}`} exposure={x} />
      ))}
    </Blueprint>
  );
}

function ExposureBlock({ exposure }: { exposure: TradeExposure }) {
  const flows = availableFlows(exposure.rows);
  const [picked, setPicked] = useState<TradeFlow | null>(null);
  const flow = picked && flows.includes(picked) ? picked : flows[0];
  const diagram = flow ? tradeDiagram(exposure.rows, flow) : null;
  const share = diagram ? topPartnerShare(diagram) : null;
  const weight = Number(exposure.exposure_weight);

  return (
    <section className="space-y-2 border-t border-border-soft pt-3 first:border-0 first:pt-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">
          HS {exposure.hs_code}
          {exposure.geography ? `, ${exposure.geography}` : ""}
          <span className="num ml-2 text-xs font-normal text-text-muted">
            {Number.isFinite(weight) ? formatPct(weight, 0) : "Weight not set"} of the business, {BASIS_LABEL[exposure.exposure_basis]?.toLowerCase() ?? exposure.exposure_basis}
          </span>
        </h3>
        {flows.length > 1 ? (
          <div role="group" aria-label="Trade direction" className="inline-flex border border-border text-xs font-semibold">
            {flows.map((f, i) => (
              <button
                key={f}
                type="button"
                aria-pressed={flow === f}
                onClick={() => setPicked(f)}
                className={cn("px-2.5 py-1.5", i > 0 && "border-l border-border", flow === f ? "bg-accent text-accent-foreground" : "text-text hover:bg-surface-muted")}
              >
                {FLOW_LABEL[f]}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {exposure.gap ? <p className="text-sm text-text-muted">{exposure.gap}</p> : null}

      {diagram ? (
        <>
          <Sankey
            nodes={diagram.nodes}
            links={diagram.links}
            height={Math.max(150, diagram.shown.length * 30)}
            formatValue={(v) => `USD ${formatCompact(v)}`}
            label={`${FLOW_LABEL[diagram.flow]} of HS ${exposure.hs_code} by partner, ${exposure.latest_period ?? "latest period"}`}
          />
          <p className="num text-xs text-text-muted">
            {exposure.latest_period ? `Period ${formatDate(exposure.latest_period)}. ` : ""}
            Largest {diagram.shown.length} partners
            {share !== null ? `; the biggest takes ${formatPct(share, 0)} of those shown` : ""}.
            {diagram.worldTotal !== null ? ` Reported world total USD ${formatCompact(diagram.worldTotal)}.` : " No world total reported."}
            {diagram.hidden > 0 ? ` ${diagram.hidden} smaller partner${diagram.hidden === 1 ? "" : "s"} not drawn.` : ""}
            {diagram.unreported > 0 ? ` ${diagram.unreported} partner${diagram.unreported === 1 ? "" : "s"} with no reported value.` : ""}
          </p>
        </>
      ) : exposure.rows.length > 0 ? (
        <p className="text-sm text-text-muted">The source reports no usable partner values for this product and direction.</p>
      ) : null}
      <p className="text-xs text-text-muted">{exposure.notice}</p>
    </section>
  );
}
