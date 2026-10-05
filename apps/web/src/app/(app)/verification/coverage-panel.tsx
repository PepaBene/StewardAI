"use client";

import { useMemo } from "react";

import { GridStatus, type GridCellStatus, type LegendEntry, Legend } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { coverageGrid, parseCoverage, type CoverageCell } from "@/lib/verification/verification";

const fetchCoverage = () => getBrowserApiClient().GET("/verification/coverage");

/** GridStatus words are fixed, so cells map: ok to ok, partial to the pale step, none to outlined. */
const CELL: Record<CoverageCell, GridCellStatus> = { ok: "ok", partial: "retried", none: "none" };

const LEGEND: LegendEntry[] = [
  { label: "Every value known", className: "bg-accent" },
  { label: "Some values unknown", className: "bg-accent-300" },
  { label: "No coverage", swatch: "outline", className: "border-border" },
];

/** Metric by jurisdiction. A missing row means no coverage; it is never drawn as a measured zero. */
export function CoveragePanel() {
  const { state } = useApiResource(fetchCoverage);
  const data = dataOf(state);
  const grid = useMemo(() => (data ? coverageGrid(parseCoverage(data)) : null), [data]);

  return (
    <Panel
      id="coverage"
      title="Measured coverage"
      description="Which metrics have measured observations in which jurisdiction, counted from the stored data itself. Metrics with the widest coverage come first."
    >
      <ResourceNotice state={state} what="measured coverage" />
      {grid && grid.rows.length === 0 ? <EmptyState title="Nothing measured yet">Coverage appears once observations are collected.</EmptyState> : null}
      {grid && grid.rows.length > 0 ? (
        <div className="space-y-2">
          <Legend items={LEGEND} className="justify-end" />
          <GridStatus
            legend={false}
            columns={grid.jurisdictions}
            rows={grid.rows.map((r) => ({ label: r.label, cells: r.cells.map((c) => CELL[c]), note: `${r.note} jurisdictions` }))}
            caption="Measured coverage by metric and jurisdiction (the screen-reader words read: OK is every value known, Retried is some values unknown, Not scheduled is no coverage)"
          />
          <p className="num text-xs text-text-muted">Columns left to right: {grid.jurisdictions.join(", ")}. Hover a cell for its metric and jurisdiction.</p>
          {grid.hiddenMetrics > 0 ? <p className="text-xs text-text-muted">{grid.hiddenMetrics} more metrics with narrower coverage are not shown.</p> : null}
        </div>
      ) : null}
    </Panel>
  );
}
