import { ProvenanceFooter } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { exposureEffect, latestObservation, rangePosition, trailingRange, type ExposureSummary } from "@/lib/macro/logic";
import type { MacroSeries } from "@/lib/macro/types";
import { cn } from "@/lib/utils";

function RangeBar({ label, low, high, value, unit }: { label: string; low: number; high: number; value: number; unit: string }) {
  const pos = rangePosition(value, { low, high });
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between text-xs text-text-muted">
        <span>{label}</span>
        <span className="num">
          {formatNumber(low, 1)} to {formatNumber(high, 1)} {unit}
        </span>
      </div>
      <div
        role="img"
        aria-label={`${label}: latest ${formatNumber(value, 1)}${pos === null ? ", range has no spread" : `, ${Math.round(pos * 100)}% of the way from low to high`}`}
        className="relative h-2 bg-surface-muted"
      >
        {pos !== null ? <span className="absolute -inset-y-0.5 w-0.5 -translate-x-1/2 bg-text" style={{ left: `${pos * 100}%` }} /> : null}
      </div>
    </div>
  );
}

/**
 * Country context for a holding or an exposure: latest value, five- and ten-year range,
 * release/as-of date, comparable countries, source, and the "how this could affect this
 * holding" line, which exists only for a mapped exposure (see `exposureEffect`).
 */
export function CountryContextCard({
  series,
  comparable,
  exposure,
  holdingName,
}: {
  series: MacroSeries;
  comparable: readonly { countryName: string; value: number | null }[];
  exposure: ExposureSummary;
  holdingName: string;
}) {
  const last = latestObservation(series);
  const r5 = trailingRange(series, 5);
  const r10 = trailingRange(series, 10);
  const effect = exposureEffect(exposure, series.country, series.countryName);
  return (
    <article className="flex flex-col gap-3 border border-border-soft p-4" aria-label={`${series.countryName}: ${series.title}`}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <p className="kicker">{series.countryName}</p>
          <h3 className="font-heading text-[19px] leading-tight font-semibold">{series.title}</h3>
        </div>
        <div className="text-right">
          <p className="num font-heading text-[30px] leading-none font-semibold">{last ? formatNumber(last.latest, 1) : "n/a"}</p>
          <p className="num text-[11px] text-text-muted">{last ? `${series.unit}, ${formatDay(last.period)}` : series.unit}</p>
        </div>
      </header>
      {last && r5 ? <RangeBar label={`Last 5 years (${r5.count} points)`} {...r5} value={last.latest} unit={series.unit} /> : null}
      {last && r10 && r10.count > (r5?.count ?? 0) ? (
        <RangeBar label={`Last 10 years (${r10.count} points)`} {...r10} value={last.latest} unit={series.unit} />
      ) : null}
      {comparable.length > 0 ? (
        <p className="text-xs text-text-muted">
          <span className="font-medium text-text">Comparable:</span>{" "}
          {comparable.map((c, i) => (
            <span key={c.countryName}>
              {i > 0 ? " · " : ""}
              {c.countryName} <span className="num">{c.value === null ? "not covered" : formatNumber(c.value, 1)}</span>
            </span>
          ))}
        </p>
      ) : null}
      <p
        data-effect={effect.kind}
        className={cn("border-l-[3px] px-3 py-1.5 text-sm", effect.kind === "mapped" ? "border-l-accent bg-accent-soft" : "border-l-border bg-surface-muted")}
      >
        <span className="font-medium">How this could affect {holdingName}: </span>
        {effect.text}
      </p>
      <ProvenanceFooter
        provenance={{
          source: `${series.provider} · ${series.title}`,
          sourceUrl: series.sourceUrl,
          asOf: last?.period ?? null,
          knownAt: last?.releasedAt ?? null,
          method: "Latest vintage; ranges over the trailing window of published values",
          state: series.state,
          reason: series.reason,
        }}
      />
    </article>
  );
}
