"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { RevisionLine, type RevisionLinePoint } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { BASIS_LABEL, BASIS_SHORT, indexView, orderSeries, seriesView, type SeriesOrder, type SeriesView } from "@/lib/macro/series";

const SELECT = "h-9 border border-border bg-surface-raised px-2 text-sm text-text focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

const ORDERS: readonly { id: SeriesOrder; label: string }[] = [
  { id: "recent", label: "Most recent data" },
  { id: "revised", label: "Largest average revision" },
  { id: "change", label: "Largest move over the window" },
];

/**
 * Macro series: country and market indicators as small multiples. Each chart draws the latest
 * value against the first value on record, so revisions are visible instead of silently
 * overwritten. Where only one vintage exists the chart says so; it never implies a revision
 * history that was not recorded.
 */
export function MacroView() {
  const [geography, setGeography] = useState(useSearchParams().get("geography") ?? "");
  const [order, setOrder] = useState<SeriesOrder>("recent");
  const [sharedScale, setSharedScale] = useState(false);
  const geo = geography.trim().toUpperCase();

  const fetcher = useMemo(
    () => () =>
      getBrowserApiClient().GET("/macro/series", {
        params: { query: { ...(geo ? { geography: geo } : {}), periods: 40, limit_series: 12 } },
      }),
    [geo],
  );
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const [asIndex, setAsIndex] = useState(false);
  const views = useMemo(() => {
    if (!data) return [];
    const base = data.map(seriesView);
    // Index mode rebases each series to 100 at its first charted period, so different units can
    // share one axis. A series with no usable base keeps null points and shows as unknown.
    const shown = asIndex ? base.map(indexView) : base;
    return orderSeries(shown, order);
  }, [data, order, asIndex]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Macro series"
        description="Indicators for the countries you are exposed to. Each chart sets the latest value against the first value on record, and marks periods that were revised."
      />

      <section aria-labelledby="macro-heading" className="blueprint space-y-4 px-5 py-5">
        <BlueprintCorners />
        <div className="flex flex-wrap items-end gap-4">
          <h2 id="macro-heading" className="mr-auto text-[22px] leading-tight font-semibold">
            Revisions by series
          </h2>
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Country code
            <Input value={geography} onChange={(e) => setGeography(e.target.value)} placeholder="All, or e.g. US, DE" maxLength={16} className="h-9 w-40" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Order by
            <select value={order} onChange={(e) => setOrder(e.target.value as SeriesOrder)} className={SELECT}>
              {ORDERS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input type="checkbox" checked={asIndex} onChange={(e) => setAsIndex(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
            Show as index (first period = 100)
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input type="checkbox" checked={sharedScale} onChange={(e) => setSharedScale(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
            Same scale for every chart
          </label>
        </div>

        <p className="max-w-[72ch] text-sm text-text-muted">
          Solid line: latest value. Dashed line: first value on record. Amber square: this period was revised. The same scale
          option puts every chart on one axis, which only makes sense for series in the same unit, so charts in other units keep their own.
        </p>

        {state.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading series…
          </p>
        ) : null}
        {state.kind === "error" ? <AlertMessage title={`Could not load series: ${state.title}`}>{state.detail}</AlertMessage> : null}
        {data && views.length === 0 ? (
          <EmptyState title="No macro observations stored">
            {geo ? `Nothing is stored for ${geo}. Clear the country code to see every series.` : "Enable a source in Config: Jobs and run the public-data job to fetch country series."}{" "}
            <Link href="/data-sources" className="text-accent-ink hover:underline">
              Open Data sources
            </Link>
            {" or "}
            <Link href="/config/jobs" className="text-accent-ink hover:underline">
              Config: Jobs
            </Link>
            .
          </EmptyState>
        ) : null}

        {views.length > 0 ? (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {views.map((v) => (
              <SeriesCard key={v.key} view={v} sharedScale={sharedScale} domain={unitDomain(views, v.unit)} />
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

/** Min and max across every series sharing `unit`, so charts of one unit can sit on one axis. */
function unitDomain(views: readonly SeriesView[], unit: string): [number, number] | undefined {
  const values = views.filter((v) => v.unit === unit).flatMap((v) => v.points.flatMap((p) => [p.first, p.latest])).filter((n): n is number => n !== null && Number.isFinite(n));
  return values.length > 0 ? [Math.min(...values), Math.max(...values)] : undefined;
}

function SeriesCard({ view, sharedScale, domain }: { view: SeriesView; sharedScale: boolean; domain: [number, number] | undefined }) {
  const points: RevisionLinePoint[] = view.points.map((p) => ({ period: p.period, first: p.first, latest: p.latest, revised: p.revised }));
  const last = view.points[view.points.length - 1];
  const fmt = (n: number) => formatNumber(n, Math.abs(n) >= 100 ? 1 : 2);
  return (
    <li className="space-y-2 border border-border px-3 py-3">
      <div>
        <h3 className="text-sm font-semibold">{view.title}</h3>
        <p className="text-xs text-text-muted">
          {view.geography} · {view.source} · {view.unit}
          {view.frequency ? ` · ${view.frequency}` : ""}
          {view.seasonalAdjustment ? ` · ${view.seasonalAdjustment}` : ""}
        </p>
      </div>
      <RevisionLine points={points} label={`${view.title}, ${view.geography}: latest value against first value on record`} formatValue={fmt} domain={sharedScale ? domain : undefined} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-text-muted">Latest ({last?.period ?? "none"})</dt>
        <dd className="num text-right">{last?.latest != null ? fmt(last.latest) : "Unknown"}</dd>
        <dt className="text-text-muted">Revised periods</dt>
        <dd className="num text-right">{view.hasVintages ? `${view.revisedPeriods} of ${view.points.length}` : "Not tracked"}</dd>
        <dt className="text-text-muted">Average revision size</dt>
        <dd className="num text-right">{view.meanAbsRevision !== null && view.hasVintages ? fmt(view.meanAbsRevision) : "Not computable"}</dd>
      </dl>
      <p className="text-xs text-text-muted" title={BASIS_LABEL[view.basis]}>
        {view.hasVintages ? BASIS_SHORT[view.basis] : "Vintage unavailable: one value stored per period, so no revision history"}
      </p>
    </li>
  );
}
