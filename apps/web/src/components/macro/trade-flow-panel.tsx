"use client";

import { useState } from "react";

import { LineChart, Meter, Sankey } from "@/components/charts";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { concentrationTrend, latestGrowth, partnerShares } from "@/lib/macro/logic";
import type { TradeSlice } from "@/lib/macro/types";

const usd = (v: number) => `$${formatCompact(v)}`;

/**
 * Bilateral flow for one reporter × product: a Sankey of product → partner for the chosen
 * year, with the unmapped remainder as its own target (never folded into a partner), and the
 * partner-concentration (HHI) trend beneath it. National trade rows say nothing about named
 * company contracts, and the panel says so.
 */
export function TradeFlowPanel({ slice }: { slice: TradeSlice }) {
  const trend = concentrationTrend(slice);
  const years = trend.map((t) => t.year);
  const [year, setYear] = useState(years[years.length - 1]);
  const p = partnerShares(slice, year);
  const growth = latestGrowth(slice);
  const hasFlow = p.totalUsd > 0;
  const productId = "product";
  const nodes = [
    { id: productId, label: `${slice.product}`, column: 0 },
    ...p.shares.map((s) => ({ id: `p:${s.partner}`, label: s.partner, column: 1 })),
    ...(p.unmappedUsd > 0 ? [{ id: "unmapped", label: "Unmapped", column: 1 }] : []),
  ];
  const links = [
    ...p.shares.map((s) => ({ source: productId, target: `p:${s.partner}`, value: s.valueUsd, emphasis: s === p.shares[0] })),
    ...(p.unmappedUsd > 0 ? [{ source: productId, target: "unmapped", value: p.unmappedUsd }] : []),
  ];
  const top = p.shares[0];
  const first = trend[0];
  const last = trend[trend.length - 1];

  return (
    <ChartFrame
      id="trade-flow"
      kicker="UN Comtrade"
      title={`${slice.reporterName} ${slice.flow}: ${slice.productName}`}
      takeaway={
        hasFlow && top
          ? `${top.partner} takes ${formatPct(top.share, 0)} of ${year} ${slice.flow}. Partner concentration ${
              last.hhi < first.hhi ? "fell" : "rose"
            } from ${first.hhi.toFixed(2)} (${first.year}) to ${last.hhi.toFixed(2)} (${last.year})${
              growth ? `; the total moved ${formatPct(growth.growth, 0, true)} in ${growth.year}` : ""
            }.`
          : "No partner account is available for this year."
      }
      units={`US dollars, ${slice.hsRevision} ${slice.product}; mapped to a partner ${formatPct(1 - (p.unmappedShare ?? 0), 0)}, unmapped ${formatPct(p.unmappedShare ?? 0, 0)}`}
      hasValue={hasFlow}
      controls={<Segmented label="Year" options={years.map((y) => ({ id: String(y), label: String(y) }))} value={String(year)} onChange={(v) => setYear(Number(v))} />}
      provenance={{
        source: `UN Comtrade · ${slice.reporterName} ${slice.flow}, ${slice.product} (${slice.hsRevision})`,
        sourceUrl: slice.sourceUrl,
        asOf: `${year}-12-31`,
        method: "Mapped partner values from reporter data; unmapped is reporter value no partner account explains. HHI over mapped partners.",
        state: p.unmappedShare !== null && p.unmappedShare > 0 ? "partial" : "verified",
        reason: "Trade rows are country flows. They do not identify any company's customers or suppliers.",
        coverage: { covered: p.shares.length, total: p.shares.length + (p.unmappedUsd > 0 ? 1 : 0), unit: "partner groups with a named partner" },
      }}
      table={{
        caption: `${slice.reporterName} ${slice.flow} of ${slice.product} by partner, ${year}`,
        columns: ["Partner", "Value (USD)", "Share of total"],
        rows: [
          ...p.shares.map((s) => [s.partner, usd(s.valueUsd), formatPct(s.share, 1)]),
          ["Unmapped (no partner account)", usd(p.unmappedUsd), formatPct(p.unmappedShare ?? 0, 1)],
        ],
      }}
    >
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div>
          <h3 className="mb-1 font-heading text-lg font-semibold">Product → partner, {year}</h3>
          <Sankey nodes={nodes} links={links} label={`${slice.product} ${slice.flow} by partner, ${year}`} formatValue={usd} labelWidth={96} height={280} />
        </div>
        <div className="flex flex-col gap-3">
          <div>
            <h3 className="mb-1 font-heading text-lg font-semibold">Partner concentration (HHI)</h3>
            <LineChart
              data={trend.map((t) => ({ date: `${t.year}-12-31`, value: t.hhi }))}
              label="HHI"
              width={420}
              height={180}
              formatValue={(v) => v.toFixed(2)}
              ariaLabel={`Partner concentration (HHI over mapped partners) from ${first.year} to ${last.year}`}
            />
            <p className="text-xs text-text-muted">1.00 = a single partner; 0.20 = five equal partners.</p>
          </div>
          <div>
            <h3 className="mb-1 font-heading text-lg font-semibold">Unmapped share</h3>
            <Meter
              label={`Unmapped share of ${year} trade`}
              value={p.unmappedShare}
              formatValue={(v) => formatPct(v, 1)}
            />
            <p className="num mt-1 text-sm font-semibold">{p.unmappedShare === null ? "n/a" : formatPct(p.unmappedShare, 1)}</p>
            <p className="text-xs text-text-muted">Part of the reporter total that no named partner explains. It is excluded from the HHI.</p>
          </div>
        </div>
      </div>
    </ChartFrame>
  );
}
