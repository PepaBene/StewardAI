"use client";

import { ConcentrationCompliance } from "@/components/portfolio/concentration-compliance";
import { DriftPanel, DriftTable } from "@/components/portfolio/drift-panel";
import { ExposurePanel } from "@/components/portfolio/exposure-panel";
import { useLookThrough } from "@/components/portfolio/look-through-exposure";
import { ReturnsHeatmap } from "@/components/portfolio/returns-heatmap";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { HISTORY_DAYS } from "@/lib/portfolio/performance";

/*
 * Panels that used to sit on the Overview page and now live on their destination pages
 * (redesign plan 5.1 "Move out"). Each fetches its own data, so a destination adds one element.
 */

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");
const fetchHistory = () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: HISTORY_DAYS } } });

/** Drift from target weights, the drift table and rule compliance: the Holdings page. */
export function DriftAndComplianceSection() {
  const { state } = useApiResource(fetchOverview);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  if (!data || !data.base) return null;
  const positions = data.allocation.filter((s) => s.key !== "CASH").map((s) => s.value_base);
  return (
    <section aria-label="Drift and rules" className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
      <DriftPanel data={data} />
      <ConcentrationCompliance positions={positions} drift={data.drift} hasStrategy={data.strategy_config_version_id !== null} />
      <div className="xl:col-span-2">
        <DriftTable data={data} />
      </div>
    </section>
  );
}

/** Effective holdings and look-through exposure: the Allocation page. */
export function LookThroughSection() {
  const { state } = useApiResource(fetchOverview);
  const lookThrough = useLookThrough().report;
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  if (!data?.drift || !data.base) return null;
  return <ExposurePanel drift={data.drift} currency={data.base.base_currency} lookThrough={lookThrough} />;
}

/** Calendar-month returns: the Performance page. */
export function MonthlyHeatmapSection() {
  const { state } = useApiResource(fetchHistory);
  const history = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const usable = history && !history.gap ? history : null;
  return <ReturnsHeatmap points={usable?.points ?? []} loading={state.kind === "loading"} />;
}
