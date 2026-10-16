"use client";

import { useMemo } from "react";

import { GridStatus, type GridCellStatus } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { coverageGrid, parseCoverage, type CoverageCell } from "@/lib/verification/verification";

const fetchCoverage = () => getBrowserApiClient().GET("/verification/coverage");

/** Coverage maps onto the matrix states: ok, partial (warning step) and none (track). */
const CELL: Record<CoverageCell, GridCellStatus> = { ok: "ok", partial: "retried", none: "none" };
const WORDS = { ok: "Every value known", retried: "Some values unknown", none: "No coverage" };

/** Metric by jurisdiction. A missing row means no coverage; it is never drawn as a measured zero. */
export function CoveragePanel() {
  const { state } = useApiResource(fetchCoverage);
  const data = dataOf(state);
  const grid = useMemo(() => (data ? coverageGrid(parseCoverage(data)) : null), [data]);

  return (
    <Panel
      id="coverage"
      rounded
      title="Measured coverage"
      description="Which metrics have measured observations in which jurisdiction, counted from the stored data itself. Metrics with the widest coverage come first."
    >
      <ResourceNotice state={state} what="measured coverage" />
      {grid && grid.rows.length === 0 ? <EmptyState title="Nothing measured yet">Coverage appears once observations are collected.</EmptyState> : null}
      {grid && grid.rows.length > 0 ? (
        <div className="space-y-2">
          <GridStatus
            words={WORDS}
            columns={grid.jurisdictions}
            rows={grid.rows.map((r) => ({ label: r.label, cells: r.cells.map((c) => CELL[c]), note: `${r.note} jurisdictions` }))}
            caption="Measured coverage by metric and jurisdiction"
          />
          <p className="num text-xs text-text-muted">Columns left to right: {grid.jurisdictions.join(", ")}. Hover a cell for its metric and jurisdiction.</p>
          {grid.hiddenMetrics > 0 ? <p className="text-xs text-text-muted">{grid.hiddenMetrics} more metrics with narrower coverage are not shown.</p> : null}
        </div>
      ) : null}
    </Panel>
  );
}
