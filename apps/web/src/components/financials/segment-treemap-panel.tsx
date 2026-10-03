"use client";

import { useState } from "react";

import { Treemap } from "@/components/charts";
import { ChartFrame, Segmented } from "@/components/data-state";
import type { HoldingSegments } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { buildSegmentSet, segmentAlignment, segmentTiles, type SegmentAxisKey, type SegmentColorBy } from "@/lib/financials/logic";
import type { FactRow } from "@/lib/financials/types";
import { AS_REPORTED_NOTE, AXIS_LABELS, availableAxes } from "@/lib/reference/segments";

const COLOR_OPTIONS = [
  { id: "growth", label: "Colour: growth" },
  { id: "margin", label: "Colour: margin" },
] as const satisfies readonly { id: SegmentColorBy; label: string }[];

/**
 * Segment treemap: area is reported revenue, as the company reports it (subtotal rows left
 * out). Colour is added only where it is comparable: growth needs a prior-year figure for the
 * same member in every tile; margin needs a filed segment profit for every tile under one
 * XBRL definition. Otherwise tiles stay neutral and the reason is printed above them.
 */
export function SegmentTreemapPanel({
  data,
  profitFacts,
  basisControl,
}: {
  data: HoldingSegments;
  profitFacts: readonly FactRow[] | null;
  /** The annual/quarter switch, rendered by the container that owns the request. */
  basisControl?: React.ReactNode;
}) {
  const axes = availableAxes(data);
  const [axisChoice, setAxisChoice] = useState<SegmentAxisKey | null>(null);
  const [colorBy, setColorBy] = useState<SegmentColorBy>("growth");
  const axis: SegmentAxisKey | null = axisChoice && axes.includes(axisChoice) ? axisChoice : (axes[0] ?? null);
  const set = axis ? buildSegmentSet(data, axis, profitFacts) : null;

  const source = data.source;
  const sourceName = source ? `${source.tier}${source.form ? ` · ${source.form}` : ""}` : "Segment revenue";
  const base = {
    id: "segments",
    kicker: "Segments · as reported",
    title: set ? `Where ${set.period} revenue comes from` : "Where revenue comes from",
  };

  if (!set) {
    return (
      <ChartFrame
        {...base}
        hasValue={false}
        controls={basisControl}
        provenance={{
          source: sourceName,
          sourceUrl: source?.filing_url ?? null,
          asOf: data.as_of,
          state: "not_covered",
          reason: data.notes?.[0] ?? "No revenue breakdown is stored for this holding and period.",
        }}
      >
        {null}
      </ChartFrame>
    );
  }
  const align = segmentAlignment(set);
  const tiles = segmentTiles(set, colorBy);
  const drawn = set.segments.reduce((s, x) => s + x.revenue, 0);
  const denominator = set.totalRevenue ?? drawn;
  const top = set.segments[0];

  return (
    <ChartFrame
      {...base}
      takeaway={`${top.name} is the largest ${AXIS_LABELS[set.axis as SegmentAxisKey].toLowerCase()} at ${formatPct(top.revenue / denominator, 0)} of revenue (${formatCompact(top.revenue)} ${set.currency}).${tiles.colored ? "" : ` Colour withheld: ${tiles.reason}`}`}
      units={`Area = reported revenue, ${set.currency}; the ${set.segments.length} tiles cover ${formatPct(Math.min(1, drawn / denominator), 0)} of ${formatCompact(denominator)} ${set.totalRevenue === null ? "(tile sum, no filed total)" : "filed total revenue"}${set.subtotalsLeftOut ? `; ${set.subtotalsLeftOut} subtotal row${set.subtotalsLeftOut === 1 ? "" : "s"} not drawn` : ""}. ${AS_REPORTED_NOTE}`}
      controls={
        <>
          {basisControl}
          {axes.length > 1 ? <Segmented label="Breakdown" value={axis as SegmentAxisKey} onChange={setAxisChoice} options={axes.map((a) => ({ id: a, label: AXIS_LABELS[a] }))} /> : null}
          <Segmented label="Tile colour" value={colorBy} onChange={setColorBy} options={COLOR_OPTIONS.map((o) => ({ ...o, disabled: !align[o.id].aligned, hint: align[o.id].reason ?? undefined }))} />
        </>
      }
      provenance={{
        source: sourceName,
        sourceUrl: source?.filing_url ?? source?.instance_url ?? null,
        asOf: set.periodEnd,
        knownAt: source?.filed ?? null,
        method: colorBy === "growth" ? "Revenue growth vs the same member one fiscal year earlier (same period length)" : "Segment operating profit ÷ segment revenue; one shared XBRL definition",
        state: tiles.colored ? (set.totalRevenue !== null && drawn < set.totalRevenue * 0.98 ? "partial" : "verified") : "partial",
        reason: tiles.colored ? null : tiles.reason,
        coverage: { covered: Math.round(drawn), total: Math.round(denominator), unit: `${set.currency} of revenue` },
      }}
      table={{
        caption: `${AXIS_LABELS[set.axis as SegmentAxisKey]} revenue ${set.period}`,
        columns: ["Segment", `Revenue (${set.currency})`, "Share", "Growth", "Margin", "Profit definition"],
        rows: set.segments.map((s) => [
          s.name,
          formatCompact(s.revenue),
          formatPct(s.revenue / denominator, 1),
          s.priorRevenue ? formatPct(s.revenue / s.priorRevenue - 1, 1, true) : "n/a",
          s.operatingProfit !== null ? formatPct(s.operatingProfit / s.revenue, 1) : "n/a",
          s.profitDefinition ?? "not filed",
        ]),
      }}
    >
      {tiles.colored ? null : (
        <p role="note" className="border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-1.5 text-sm">
          Not coloured: {tiles.reason}
        </p>
      )}
      <Treemap
        items={tiles.items}
        cap={tiles.cap}
        scale={tiles.colored}
        aspectRatio={2.6}
        label={`Revenue by ${AXIS_LABELS[set.axis as SegmentAxisKey].toLowerCase()}, sized by revenue${tiles.colored ? `, coloured by ${colorBy}` : ", uncoloured"}`}
        formatWeight={(w) => formatCompact(w)}
        formatChange={(c) => formatPct(c, 1, colorBy === "growth")}
      />
    </ChartFrame>
  );
}
