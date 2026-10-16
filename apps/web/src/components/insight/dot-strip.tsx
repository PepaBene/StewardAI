"use client";

import { useCallback, useState } from "react";

import { AXIS_TEXT, SrTable, SVG_BASE } from "@/components/charts/shared";
import { LimitedState } from "@/components/insight/limited-state";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

/*
 * F20 DotStrip (visual spec B19): every item as a dot on one horizontal scale. Dots are labelled
 * directly (two lanes, above and below, so labels never collide; at most 12 labelled, the rest via
 * tooltip), optionally sized by weight, reference lines (portfolio, benchmark) carry their own
 * label, and the domain is the 2nd to 98th percentile padded by 10% so one outlier cannot flatten
 * the rest: values beyond it sit on the edge as a chevron with their value. Below three placed
 * points the strip says so (LimitedState) instead of drawing a lonely dot.
 */

export type StripDot = { key: string; label: string; value: number | null; highlight?: boolean; weight?: number | null };
export type StripRef = { label: string; value: number; dashed?: boolean };
export type StripZone = { from: number; to: number; tone: "gain" | "loss" | "neutral"; label: string };

const ZONE_FILL = { gain: "fill-gain-subtle", loss: "fill-loss-subtle", neutral: "fill-surface-muted" } as const;

export const MIN_PLACED = 3;
export const MAX_LABELLED = 12;

/** Price versus fair value, (price - fair) / fair: negative = cheaper = left = gain zone. Null when fair is not positive. */
export function priceVsFair(price: number | null | undefined, fair: number | null | undefined): number | null {
  if (price == null || fair == null || !Number.isFinite(price) || !Number.isFinite(fair) || fair <= 0) return null;
  return (price - fair) / fair;
}

/** Linear-interpolated quantile `q` (0..1) of an ascending array. */
export function quantile(sorted: readonly number[], q: number): number {
  if (sorted.length === 0) return NaN;
  const pos = Math.max(0, Math.min(1, q)) * (sorted.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** The strip's domain: p2..p98 of `values` padded by 10% of the span (a flat set gets +-1). */
export function winsorDomain(values: readonly number[], pad = 0.1): [number, number] | null {
  const v = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) return null;
  let lo = quantile(v, 0.02);
  let hi = quantile(v, 0.98);
  if (hi - lo < 1e-12) return [lo - 1, hi + 1];
  const span = hi - lo;
  lo -= span * pad;
  hi += span * pad;
  return [lo, hi];
}

/** Where `value` lands in `domain`: clamped to the edge, with which edge when it was outside. */
export function clipToDomain(value: number, domain: readonly [number, number]): { value: number; clipped: "low" | "high" | null } {
  if (value < domain[0]) return { value: domain[0], clipped: "low" };
  if (value > domain[1]) return { value: domain[1], clipped: "high" };
  return { value, clipped: null };
}

type Placed = StripDot & { value: number; clipped: "low" | "high" | null; x: number; r: number };
type Label = { text: string; x: number; lane: "above" | "below"; anchor: "start" | "middle" | "end" };

const CHAR_W = 6.1;

/** Greedy two-lane label placement in priority order; labels that fit nowhere are dropped (tooltip only). */
export function placeLabels(
  items: readonly { key: string; text: string; x: number }[],
  bounds: readonly [number, number],
  max = MAX_LABELLED,
): Map<string, Label> {
  const out = new Map<string, Label>();
  const lanes: Record<"above" | "below", [number, number][]> = { above: [], below: [] };
  for (const it of items) {
    if (out.size >= max) break;
    const w = it.text.length * CHAR_W;
    const anchor: Label["anchor"] = it.x - w / 2 < bounds[0] ? "start" : it.x + w / 2 > bounds[1] ? "end" : "middle";
    const left = anchor === "start" ? it.x : anchor === "end" ? it.x - w : it.x - w / 2;
    const span: [number, number] = [left - 4, left + w + 4];
    for (const lane of ["above", "below"] as const) {
      if (lanes[lane].every(([a, b]) => span[1] <= a || span[0] >= b)) {
        lanes[lane].push(span);
        out.set(it.key, { text: it.text, x: it.x, lane, anchor });
        break;
      }
    }
  }
  return out;
}

export function DotStrip({
  dots,
  refs = [],
  zones = [],
  format,
  label,
  convention,
  sizeByWeight = false,
  className,
}: {
  dots: readonly StripDot[];
  refs?: readonly StripRef[];
  zones?: readonly StripZone[];
  format: (v: number) => string;
  label: string;
  /** "price-vs-fair": values are priceVsFair(); negative = cheaper = left, with labelled gain / loss zones. */
  convention?: "price-vs-fair";
  /** Dot area proportional to `weight` (8-20 px across). */
  sizeByWeight?: boolean;
  className?: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  // Drawn at the container's own width so the 11 px labels stay 11 px instead of scaling with the card.
  const [W, setW] = useState(640);
  const figureRef = useCallback((el: HTMLElement | null) => {
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setW(Math.max(320, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const known = dots.filter((d): d is StripDot & { value: number } => d.value !== null && Number.isFinite(d.value));
  const missing = dots.filter((d) => d.value === null || !Number.isFinite(d.value));
  if (known.length < MIN_PLACED) {
    return (
      <LimitedState className={className}>
        Only {known.length} {known.length === 1 ? "holding has" : "holdings have"} this figure; the strip needs {MIN_PLACED}.
        {missing.length ? ` Not placed: ${missing.map((m) => m.label).join(", ")}.` : ""}
      </LimitedState>
    );
  }

  const padX = 18;
  const cy = 72;
  const zoneBottom = cy + 34;
  const H = zoneBottom + 22;
  const base = winsorDomain(known.map((d) => d.value))!;
  const withRefs = [...base, ...refs.map((r) => r.value), ...(convention ? [0] : [])];
  const domain: [number, number] = [Math.min(...withRefs), Math.max(...withRefs)];
  const x = linearScale(domain, [padX, W - padX]);
  const ticks = niceTicks(domain[0], domain[1], 5).filter((t) => t >= domain[0] - 1e-9 && t <= domain[1] + 1e-9);

  const weights = known.map((d) => d.weight).filter((w): w is number => w != null && Number.isFinite(w) && w > 0);
  const [wMin, wMax] = weights.length ? [Math.min(...weights), Math.max(...weights)] : [0, 0];
  const radius = (d: StripDot) => {
    const w = d.weight;
    if (!sizeByWeight || w == null || !(w > 0) || wMax <= wMin) return 5;
    const t = (w - wMin) / (wMax - wMin);
    return Math.sqrt(64 + (400 - 64) * t) / 2; // area-proportional, 8-20 px across
  };
  const placed: Placed[] = known
    .map((d) => {
      const c = clipToDomain(d.value, domain);
      return { ...d, value: d.value, clipped: c.clipped, x: x(c.value), r: radius(d) };
    })
    .sort((a, b) => a.value - b.value);

  // Label priority: highlighted, clipped (they must show their value), the two extremes, then heaviest.
  const lo = placed[0]?.key;
  const hi = placed[placed.length - 1]?.key;
  const rank = (d: Placed) => (d.highlight ? 0 : d.clipped ? 1 : d.key === lo || d.key === hi ? 2 : 3);
  const byPriority = [...placed].sort((a, b) => rank(a) - rank(b) || (b.weight ?? 0) - (a.weight ?? 0) || Math.abs(b.value) - Math.abs(a.value));
  // More than 12 dots: the must-show ones (highlight, clipped, extremes) plus the 6 largest; the rest via tooltip.
  const capped = placed.length > MAX_LABELLED ? byPriority.slice(0, byPriority.filter((d) => rank(d) < 3).length + 6) : byPriority;
  const labels = placeLabels(
    capped.map((d) => ({ key: d.key, text: d.clipped ? `${d.label} ${format(d.value)}` : d.label, x: d.x })),
    [2, W - 2],
  );

  const autoZones: StripZone[] = convention
    ? [
        { from: domain[0], to: 0, tone: "gain", label: "Cheaper than fair value" },
        { from: 0, to: domain[1], tone: "loss", label: "Dearer than fair value" },
      ]
    : zones.slice();
  const hovered = placed.find((d) => d.key === hover);
  const sortedRefs = [...refs].sort((a, b) => a.value - b.value);

  return (
    <figure ref={figureRef} className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} aria-hidden="true">
        {autoZones.map((z) => {
          const x0 = x(Math.max(domain[0], z.from));
          const x1 = x(Math.min(domain[1], z.to));
          if (x1 <= x0) return null;
          return (
            <g key={z.label}>
              <rect x={x0} y={4} width={x1 - x0} height={zoneBottom - 4} className={ZONE_FILL[z.tone]} />
              <text x={x0 + 6} y={16} fontSize={11} className="fill-text-muted">
                {z.label}
              </text>
            </g>
          );
        })}
        <line x1={padX} x2={W - padX} y1={cy} y2={cy} className="stroke-border" />
        <line x1={padX} x2={W - padX} y1={zoneBottom} y2={zoneBottom} className="stroke-border-soft" />
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 6} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
            {format(t)}
          </text>
        ))}
        {sortedRefs.map((r, i) => {
          const rx = x(r.value);
          const text = `${r.label} ${format(r.value)}`;
          const flip = rx + 6 + text.length * CHAR_W > W - 2;
          return (
            <g key={r.label}>
              <line x1={rx} x2={rx} y1={22} y2={zoneBottom} className="stroke-text-muted" strokeWidth={1} strokeDasharray="3 3" />
              <text x={flip ? rx - 4 : rx + 4} y={30 + (i % 2) * 11} textAnchor={flip ? "end" : "start"} fontSize={11} className="fill-text-muted">
                {text}
              </text>
            </g>
          );
        })}
        {placed.map((d) => {
          const on = d.highlight || hover === d.key;
          const fill = d.highlight ? "fill-accent" : "fill-chart-benchmark";
          const tip = `${d.label}: ${format(d.value)}`;
          if (d.clipped) {
            const dir = d.clipped === "high" ? 1 : -1;
            return (
              <path key={d.key} d={`M${d.x - dir * 6},${cy - 6} L${d.x + dir * 2},${cy} L${d.x - dir * 6},${cy + 6} Z`} className={cn(fill, "stroke-surface")} strokeWidth={1.5}>
                <title>{tip}</title>
              </path>
            );
          }
          return (
            <circle key={d.key} cx={d.x} cy={cy} r={on ? d.r + 1 : d.r} className={cn(fill, "stroke-surface")} strokeWidth={2}>
              <title>{tip}</title>
            </circle>
          );
        })}
        {[...labels.entries()].map(([key, l]) => {
          const d = placed.find((p) => p.key === key)!;
          const y = l.lane === "above" ? cy - d.r - 8 : cy + d.r + 16;
          return (
            <text key={key} x={l.x} y={y} textAnchor={l.anchor} fontSize={11} className={cn(d.highlight ? "fill-accent-ink font-semibold" : "fill-text")}>
              {l.text}
            </text>
          );
        })}
        {hovered && !labels.has(hovered.key) ? (
          <text x={hovered.x} y={cy - hovered.r - 8} textAnchor="middle" fontSize={12} strokeWidth={3} paintOrder="stroke" className="fill-text stroke-surface font-semibold">
            {hovered.label} {format(hovered.value)}
          </text>
        ) : null}
      </svg>
      {/* Keyboard and pointer targets for the dots without a direct label, larger than the dot itself. */}
      {placed.some((d) => !labels.has(d.key)) ? (
      <ul className="flex flex-wrap items-center gap-1 text-xs" aria-label="Dots without a label">
        <li className="text-text-muted">Also placed:</li>
        {placed.filter((d) => !labels.has(d.key)).map((d) => (
          <li key={d.key}>
            <button
              type="button"
              title={`${d.label}: ${format(d.value)}`}
              onMouseEnter={() => setHover(d.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(d.key)}
              onBlur={() => setHover(null)}
              className={cn("rounded-mark border px-1.5 py-0.5", d.highlight ? "border-accent text-accent-ink" : "border-border-soft text-text-muted")}
            >
              {d.label}
            </button>
          </li>
        ))}
      </ul>
      ) : null}
      {missing.length ? <p className="text-xs text-text-faint">Not placed ({missing.length}, no value): {missing.map((m) => m.label).join(", ")}</p> : null}
      <SrTable caption={label} columns={["Item", "Value"]} rows={placed.map((d) => [d.label, format(d.value)])} />
    </figure>
  );
}
