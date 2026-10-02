"use client";

import { useState } from "react";

import { Treemap } from "@/components/charts";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { segmentAlignment, segmentTiles, type SegmentColorBy } from "@/lib/financials/logic";
import type { SegmentSet } from "@/lib/financials/types";

const OPTIONS = [
  { id: "growth", label: "Colour: growth" },
  { id: "margin", label: "Colour: margin" },
] as const satisfies readonly { id: SegmentColorBy; label: string }[];

/**
 * Segment treemap: area is reported revenue. Colour is added only where the metric is
 * comparable across segments (a prior year for growth; one profit definition for margin);
 * otherwise the tiles stay neutral and the reason is printed above them.
 */
export function SegmentTreemapPanel({ set, sourceUrl }: { set: SegmentSet; sourceUrl: string | null }) {
  const [colorBy, setColorBy] = useState<SegmentColorBy>("growth");
  const align = segmentAlignment(set);
  const tiles = segmentTiles(set, colorBy);
  const sorted = [...set.segments].sort((a, b) => b.revenue - a.revenue);
  const explained = set.segments.reduce((s, x) => s + x.revenue, 0);

  return (
    <ChartFrame
      id="segments"
      kicker="Segments"
      title={`Where ${set.period} revenue comes from`}
      takeaway={`${sorted[0].name} is the largest segment at ${formatPct(sorted[0].revenue / set.totalRevenue, 0)} of revenue (${formatCompact(sorted[0].revenue)} ${set.currency}m).${tiles.colored ? "" : ` Colour withheld: ${tiles.reason}`}`}
      units={`Area = reported revenue, ${set.currency} millions; segments cover ${formatPct(explained / set.totalRevenue, 0)} of ${formatCompact(set.totalRevenue)} total revenue`}
      controls={
        <Segmented
          label="Tile colour"
          value={colorBy}
          onChange={setColorBy}
          options={OPTIONS.map((o) => ({ ...o, disabled: !align[o.id].aligned, hint: align[o.id].reason ?? undefined }))}
        />
      }
      provenance={{
        source: set.citation,
        sourceUrl,
        asOf: null,
        method: colorBy === "growth" ? "Revenue growth vs prior fiscal year, per segment" : "Segment operating profit ÷ segment revenue, one shared definition",
        state: tiles.colored ? "verified" : "partial",
        reason: tiles.colored ? null : tiles.reason,
        coverage: { covered: Math.round(explained), total: Math.round(set.totalRevenue), unit: `${set.currency}m of revenue` },
      }}
      table={{
        caption: `Segments ${set.period}`,
        columns: ["Segment", `Revenue (${set.currency}m)`, "Share", "Growth", "Margin", "Profit definition"],
        rows: sorted.map((s) => [
          s.name,
          formatCompact(s.revenue),
          formatPct(s.revenue / set.totalRevenue, 1),
          s.priorRevenue ? formatPct(s.revenue / s.priorRevenue - 1, 1, true) : "n/a",
          s.operatingProfit !== null ? formatPct(s.operatingProfit / s.revenue, 1) : "n/a",
          s.profitDefinition ?? "not reported",
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
        label={`Segments sized by revenue${tiles.colored ? `, coloured by ${colorBy}` : ", uncoloured"}`}
        formatWeight={(w) => formatCompact(w)}
        formatChange={(c) => formatPct(c, 1, colorBy === "growth")}
      />
    </ChartFrame>
  );
}
