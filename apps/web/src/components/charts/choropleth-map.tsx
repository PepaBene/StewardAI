"use client";

import { useId, useMemo, useState, type ReactNode } from "react";

import { choroplethBreaks, countryName, RAMP_BG, RAMP_FILL, rampIndex } from "@/lib/charts/geo";
import { MAP_HEIGHT, MAP_WIDTH, worldShapes } from "@/lib/charts/geo-world";
import { isFiniteNumber } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { ChartEmpty, SVG_BASE } from "./shared";

export type ChoroplethDatum = {
  /** ISO 3166-1 alpha-2 country code (resolve other spellings with resolveGeography first). */
  code: string;
  /** Null: the country is known but has no value (drawn like "no data", never as zero). 0 is a real zero. */
  value: number | null;
  /** Extra tooltip line, e.g. "Reported 40%, estimated 12%". */
  detail?: ReactNode;
};

/** A ranked row that has no polygon: an aggregate (EU) or the explicit unknown remainder. */
export type ChoroplethExtraRow = {
  key: string;
  label: ReactNode;
  value: number | null;
  /** "unknown" draws a dashed track: the share nothing explains. */
  kind?: "aggregate" | "unknown";
  note?: ReactNode;
};

export type ChoroplethProps = {
  data: readonly ChoroplethDatum[];
  formatValue: (value: number) => string;
  /** Legend heading with the unit, e.g. "% of portfolio value". */
  unitLabel: string;
  /** The denominator in words, shown under the legend. */
  denominator?: ReactNode;
  /** Names the map for assistive technology. */
  ariaLabel: string;
  noDataLabel?: string;
  zeroLabel?: string;
  extraRows?: readonly ChoroplethExtraRow[];
  /** Fixed top of the scale (e.g. 1 for a share); defaults to the largest value. */
  className?: string;
};

type Active = { key: string; name: string; code: string | null; anchor: [number, number] | null };

/**
 * A choropleth of countries (Natural Earth 110m, projected by hand in lib/charts/geo): a
 * sequential accent ramp for values, a plain outlined fill for a real zero and a hatched fill
 * for "no data / not covered". It is an overview only: the ranked list beside it carries the
 * precise comparison, and callers add an exact table. Countries with data take keyboard focus
 * and show a tooltip on focus and hover. This module imports the world topology, so use it
 * through the lazy `Choropleth` export.
 */
export function ChoroplethMap({
  data,
  formatValue,
  unitLabel,
  denominator,
  ariaLabel,
  noDataLabel = "No data / not covered",
  zeroLabel = "Zero",
  extraRows = [],
  className,
}: ChoroplethProps) {
  const uid = useId().replace(/:/g, "");
  const hatchId = `hatch-${uid}`;
  const shapes = useMemo(() => worldShapes(), []);
  const byCode = useMemo(() => new Map(data.map((d) => [d.code, d])), [data]);
  const breaks = useMemo(() => choroplethBreaks(data.map((d) => d.value)), [data]);
  const [active, setActive] = useState<Active | null>(null);

  const drawn = new Set(shapes.map((s) => s.code).filter((c): c is string => c !== null));
  const offMap = data.filter((d) => !drawn.has(d.code));
  const ranked = [...data]
    .filter((d): d is ChoroplethDatum & { value: number } => isFiniteNumber(d.value))
    .sort((a, b) => b.value - a.value || a.code.localeCompare(b.code));
  const top = ranked.length ? ranked[0].value : 0;

  if (data.length === 0 && extraRows.length === 0) return <ChartEmpty label="No country values to map" minHeight={160} />;

  const activeDatum = active?.code ? byCode.get(active.code) : undefined;
  const tooltip = active && active.anchor ? active : null;

  return (
    <div className={cn("grid min-w-0 grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]", className)}>
      <div className="min-w-0">
        <div className="relative">
          <svg
            viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
            className={SVG_BASE}
            role="group"
            aria-label={ariaLabel}
            onKeyDown={(e) => {
              if (e.key === "Escape") setActive(null);
            }}
          >
            <defs>
              <pattern id={hatchId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" className="fill-surface-muted" />
                <line x1="0" y1="0" x2="0" y2="6" className="stroke-text-faint" strokeWidth="1.5" />
              </pattern>
            </defs>
            {shapes.map((s) => {
              const d = s.code ? byCode.get(s.code) : undefined;
              const key = s.id ?? s.name;
              const idx = d ? rampIndex(d.value, breaks) : null;
              const hasValue = d !== undefined && isFiniteNumber(d.value);
              const isZero = hasValue && d.value === 0;
              const fillClass = idx !== null ? RAMP_FILL[idx] : isZero ? "fill-surface-raised" : null;
              const label = hasValue ? `${s.name}: ${isZero ? zeroLabel : formatValue(d.value as number)}` : `${s.name}: ${noDataLabel}`;
              const isActive = active?.key === key;
              const focusable = hasValue;
              return (
                <path
                  key={key}
                  d={s.d}
                  fill={fillClass ? undefined : `url(#${hatchId})`}
                  className={cn(
                    fillClass,
                    isZero ? "stroke-text" : "stroke-border",
                    isActive && "stroke-text",
                    "outline-hidden focus-visible:stroke-focus",
                  )}
                  strokeWidth={isActive || isZero ? 1.5 : 0.5}
                  strokeLinejoin="round"
                  tabIndex={focusable ? 0 : undefined}
                  role={focusable ? "img" : undefined}
                  aria-label={focusable ? label : undefined}
                  aria-hidden={focusable ? undefined : true}
                  onMouseEnter={() => setActive({ key, name: s.name, code: s.code, anchor: s.anchor })}
                  onMouseLeave={() => setActive((a) => (a?.key === key ? null : a))}
                  onFocus={() => setActive({ key, name: s.name, code: s.code, anchor: s.anchor })}
                  onBlur={() => setActive((a) => (a?.key === key ? null : a))}
                >
                  <title>{label}</title>
                </path>
              );
            })}
          </svg>
          {tooltip && tooltip.anchor ? (
            <div
              role="status"
              className="pointer-events-none absolute z-10 max-w-48 -translate-x-1/2 -translate-y-full border border-border bg-surface-raised px-2 py-1 text-xs shadow-sm"
              style={{ left: `${(tooltip.anchor[0] / MAP_WIDTH) * 100}%`, top: `${(tooltip.anchor[1] / MAP_HEIGHT) * 100}%` }}
            >
              <span className="block font-medium text-text">{tooltip.name}</span>
              <span className="num block text-text-muted">
                {activeDatum && isFiniteNumber(activeDatum.value)
                  ? activeDatum.value === 0
                    ? `${zeroLabel} (mapped, none)`
                    : formatValue(activeDatum.value)
                  : noDataLabel}
              </span>
              {activeDatum?.detail ? <span className="block text-text-muted">{activeDatum.detail}</span> : null}
            </div>
          ) : null}
        </div>

        <div className="mt-2 flex flex-col gap-1.5 text-[11px] text-text-muted">
          <p className="font-medium text-text">{unitLabel}</p>
          <ul className="m-0 flex list-none flex-wrap items-center gap-x-3 gap-y-1 p-0">
            {breaks
              ? [...breaks.edges.keys()].map((i) => {
                  const lo = i === 0 ? 0 : breaks.edges[i - 1];
                  return (
                    <li key={i} className="num inline-flex items-center gap-1.5">
                      <span aria-hidden="true" className={cn("inline-block size-3 border border-border-soft", RAMP_BG[RAMP_BG.length - 1 - i])} />
                      {i === 0 ? `up to ${formatValue(breaks.edges[0])}` : `${formatValue(lo)} to ${formatValue(breaks.edges[i])}`}
                    </li>
                  );
                })
              : null}
            <li className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block size-3 border-2 border-text bg-surface-raised" />
              {zeroLabel} (mapped, none)
            </li>
            <li className="inline-flex items-center gap-1.5">
              <svg aria-hidden="true" width="14" height="14" className="border border-border">
                <rect width="14" height="14" fill={`url(#${hatchId})`} />
              </svg>
              {noDataLabel}
            </li>
          </ul>
          {denominator ? <p>{denominator}</p> : null}
          <p>Equal-interval classes from zero. The ranked list gives exact values; the map is an overview.</p>
        </div>
      </div>

      <div className="min-w-0">
        <ol aria-label={`${unitLabel}, ranked`} className="m-0 flex list-none flex-col gap-1.5 p-0">
          {ranked.map((r) => (
            <li
              key={r.code}
              className="grid grid-cols-[minmax(5rem,9rem)_minmax(0,1fr)_3.75rem] items-center gap-2 text-sm"
              onMouseEnter={() => {
                const s = shapes.find((x) => x.code === r.code);
                setActive(s ? { key: s.id ?? s.name, name: s.name, code: s.code, anchor: s.anchor } : null);
              }}
              onMouseLeave={() => setActive(null)}
            >
              <span className="truncate" title={countryName(r.code)}>
                {countryName(r.code)}
              </span>
              <span className="flex h-2 bg-surface-muted" aria-hidden="true">
                <span className="block h-full bg-accent" style={{ width: `${top > 0 ? Math.min(100, (r.value / top) * 100) : 0}%` }} />
              </span>
              <span className="num text-right">{formatValue(r.value)}</span>
            </li>
          ))}
          {offMap.map((d) => (
            <li key={d.code} className="grid grid-cols-[minmax(5rem,9rem)_minmax(0,1fr)_3.75rem] items-center gap-2 text-sm text-text-muted">
              <span className="truncate">{countryName(d.code)}</span>
              <span className="text-[11px]">not drawn on the map</span>
              <span className="num text-right">{isFiniteNumber(d.value) ? formatValue(d.value) : "n/a"}</span>
            </li>
          ))}
          {extraRows.map((r) => (
            <li key={r.key} className="grid grid-cols-[minmax(5rem,9rem)_minmax(0,1fr)_3.75rem] items-center gap-2 text-sm">
              <span className={cn("truncate", r.kind === "unknown" && "text-text-muted")}>{r.label}</span>
              <span
                className={cn("flex h-2", r.kind === "unknown" ? "border border-dashed border-border" : "bg-surface-muted")}
                aria-hidden="true"
              >
                {isFiniteNumber(r.value) ? (
                  <span
                    className={cn("block h-full", r.kind === "unknown" ? "bg-border" : "bg-accent")}
                    style={{ width: `${top > 0 || r.kind === "unknown" ? Math.min(100, (r.value / Math.max(top, r.value, 1e-12)) * 100) : 0}%` }}
                  />
                ) : null}
              </span>
              <span className={cn("num text-right", r.kind === "unknown" && "text-text-muted")}>{isFiniteNumber(r.value) ? formatValue(r.value) : "n/a"}</span>
              {r.note ? <span className="col-span-3 -mt-1 text-[11px] text-text-muted">{r.note}</span> : null}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
