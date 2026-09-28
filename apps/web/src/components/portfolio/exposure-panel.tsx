import { StackedBar } from "@/components/charts";
import { AllocationBars, type AllocationItem } from "@/components/portfolio/allocation-bars";
import { DriftBar } from "@/components/portfolio/drift-bar";
import { DriftStatusBadge } from "@/components/portfolio/drift-status-badge";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { DriftReport } from "@/lib/api/client";
import { formatPercent, formatPp } from "@/lib/format";

const ORDER = ["sector", "geography", "region", "currency", "asset_class"];
const TITLES: Record<string, string> = { geography: "Region", asset_class: "Asset class" };
const NOTES: Record<string, string> = { currency: "Trading currency" };

function title(dimension: string) {
  const t = TITLES[dimension] ?? dimension.replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/**
 * Exposure by each classification dimension of the strategy (sector, region, currency, …) as
 * one 100% bar each, cash included so the parts add up to the weight basis; buckets with a
 * target list their drift and status beneath.
 */
export function ExposurePanel({ drift, currency }: { drift: DriftReport; currency: string }) {
  const dims = drift.categories
    .filter((c) => c.buckets.some((b) => b.bucket !== "Unclassified" || b.target_weight !== null))
    .sort((a, b) => {
      const ia = ORDER.indexOf(a.dimension);
      const ib = ORDER.indexOf(b.dimension);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  const cash = drift.weight_basis === "total" && drift.cash.value_base > 0 ? drift.cash.value_base : 0;

  return (
    <Blueprint className="flex min-w-0 flex-col gap-4.5 px-6 py-5.5" aria-label="Exposure by category">
      <PanelHeading kicker="Exposure" title={dims.length ? dims.slice(0, 3).map((d) => title(d.dimension).toLowerCase()).join(", ").replace(/^./, (c) => c.toUpperCase()) : "By category"} />
      {dims.length === 0 ? (
        <p className="text-sm text-text-muted">No holdings are classified yet (strategy config: categories).</p>
      ) : null}
      {dims.map((d) => {
        const buckets = [...d.buckets].sort((a, b) => b.value_base - a.value_base);
        const largest = buckets.find((b) => b.bucket !== "Unclassified");
        const segments = [...buckets.map((b) => ({ label: b.bucket, value: b.value_base })), ...(cash ? [{ label: "Cash", value: cash }] : [])];
        const targeted = d.buckets.filter((b) => b.status !== "no_target");
        return (
          <div key={d.dimension} className="flex flex-col gap-2">
            <StackedBar
              segments={segments}
              title={title(d.dimension)}
              note={NOTES[d.dimension] ?? (largest ? `Largest: ${largest.bucket}` : undefined)}
            />
            {targeted.length ? (
              <ul className="space-y-1 text-xs" aria-label={`${title(d.dimension)} against targets`}>
                {targeted.map((b) => (
                  <li key={b.bucket} className="flex flex-wrap items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-2">
                      <DriftBar drift={b.drift} scale={0.05} className="w-14" />
                      <span>
                        {b.bucket}: {formatPercent(b.weight)}, target {formatPercent(b.target_weight)}, drift{" "}
                        <span className="num">{formatPp(b.drift)}</span>
                      </span>
                    </span>
                    <DriftStatusBadge status={b.status} />
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
      {dims.length ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-medium text-accent-ink hover:underline">Amounts by category</summary>
          <div className="mt-3 space-y-5">
            {dims.map((d) => {
              const items: AllocationItem[] = d.buckets.map((b) => ({
                key: b.bucket,
                label: b.bucket,
                value: b.value_base,
                weight: b.weight,
                muted: b.bucket === "Unclassified",
              }));
              return (
                <div key={d.dimension} className="space-y-2">
                  <h3 className="text-xs font-semibold">{title(d.dimension)}</h3>
                  <AllocationBars items={items} currency={currency} label={`Allocation by ${title(d.dimension).toLowerCase()}`} />
                </div>
              );
            })}
          </div>
        </details>
      ) : null}
      <p className="text-[11px] text-text-faint">
        Currency is each instrument&rsquo;s trading currency; ETFs are classified as a whole (no look-through).
      </p>
    </Blueprint>
  );
}
