"use client";

import { annularSector } from "@/lib/charts/arc";
import { AXIS_LABEL, petalAngles, petalPath, type MarkAxis, type MarkPetal } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F18 ScoreMark: the one glyph for a stock's five-axis profile. Each axis is a petal filled from
 * the hub outward in proportion to its score; the fill steps along the score-mark ramp so a strong
 * axis also reads darker. An unknown axis is an open dashed gap (never an empty or zero petal),
 * a not-applicable axis a faint dotted outline. Sizes: "row" 24 px, "header" 64 px, "card" up to
 * 300 px with axis labels and clickable axis points.
 */

export type ScoreMarkSize = "row" | "header" | "card";

const SIZE: Record<ScoreMarkSize, number> = { row: 24, header: 64, card: 300 };

function petalClass(fraction: number): string {
  if (fraction >= 0.75) return "fill-score-mark-strong";
  if (fraction >= 0.4) return "fill-score-mark";
  return "fill-score-mark-soft";
}

function describe(petals: readonly MarkPetal[]): string {
  return petals
    .map((p) =>
      p.notApplicable
        ? `${AXIS_LABEL[p.axis]} not applicable`
        : p.fraction === null
          ? `${AXIS_LABEL[p.axis]} unknown`
          : `${AXIS_LABEL[p.axis]} ${Math.round(p.fraction * 6 * 10) / 10} of 6`,
    )
    .join(", ");
}

export function ScoreMark({
  petals,
  size = "row",
  selected,
  onSelect,
  label,
  className,
}: {
  petals: readonly MarkPetal[];
  size?: ScoreMarkSize;
  /** Highlighted axis in the card form. */
  selected?: MarkAxis | null;
  /** Makes the axis points buttons (card form only). */
  onSelect?: (axis: MarkAxis) => void;
  /** Accessible name prefix, e.g. the company name. */
  label?: string;
  className?: string;
}) {
  const px = SIZE[size];
  const big = size === "card";
  const V = big ? 300 : 100;
  const c = V / 2;
  const rim = big ? 104 : 47;
  const hub = big ? 14 : 8;
  const stroke = big ? 1.5 : size === "header" ? 2.4 : 5;
  const summary = `${label ? `${label}: ` : ""}${describe(petals)}`;

  return (
    <span
      className={cn("relative inline-block shrink-0 align-middle", className)}
      style={big ? { width: "100%", maxWidth: px } : { width: px, height: px }}
    >
      <svg viewBox={`0 0 ${V} ${V}`} className="block h-auto w-full overflow-visible" role="img" aria-label={summary}>
        <title>{summary}</title>
        {petals.map((p, i) => {
          const [a0, a1] = petalAngles(i);
          const track = annularSector(c, c, hub, rim, a0, a1);
          const isSel = selected === p.axis;
          if (p.notApplicable) {
            return (
              <path key={p.axis} d={track} fill="none" className="stroke-score-mark-gap" strokeWidth={stroke * 0.6} strokeDasharray={big ? "1 4" : "0.5 3"} />
            );
          }
          if (p.fraction === null) {
            return (
              <path key={p.axis} d={track} fill="none" className="stroke-score-mark-gap" strokeWidth={stroke * 0.7} strokeDasharray={big ? "6 5" : "3 3"} />
            );
          }
          return (
            <g key={p.axis} opacity={selected && !isSel ? 0.45 : 1}>
              <path d={track} className="fill-score-mark-track" />
              {p.fraction > 0 ? <path d={petalPath(i, p.fraction, c, c, hub, rim)} className={petalClass(p.fraction)} /> : null}
            </g>
          );
        })}
        {big
          ? petals.map((p, i) => {
              const [a0, a1] = petalAngles(i);
              const mid = (a0 + a1) / 2;
              const lx = c + (rim + 26) * Math.cos(mid);
              const ly = c + (rim + 26) * Math.sin(mid);
              const anchor = Math.abs(Math.cos(mid)) < 0.25 ? "middle" : Math.cos(mid) > 0 ? "start" : "end";
              return (
                <text
                  key={p.axis}
                  x={lx}
                  y={ly + 4}
                  textAnchor={anchor}
                  fontSize={13}
                  className={cn(selected === p.axis ? "fill-text font-semibold" : "fill-text-muted")}
                >
                  {AXIS_LABEL[p.axis]}
                </text>
              );
            })
          : null}
      </svg>
      {big && onSelect ? (
        <span className="absolute inset-0">
          {petals.map((p, i) => {
            const [a0, a1] = petalAngles(i);
            const mid = (a0 + a1) / 2;
            const r = (rim + 26) / V;
            const left = 50 + r * 100 * Math.cos(mid);
            const top = 50 + r * 100 * Math.sin(mid);
            return (
              <button
                key={p.axis}
                type="button"
                aria-pressed={selected === p.axis}
                aria-label={`Show ${AXIS_LABEL[p.axis]}`}
                onClick={() => onSelect(p.axis)}
                className="absolute size-11 -translate-x-1/2 -translate-y-1/2 rounded-full focus-visible:outline-2 focus-visible:outline-accent"
                style={{ left: `${left}%`, top: `${top}%` }}
              />
            );
          })}
        </span>
      ) : null}
    </span>
  );
}

/** Legend for the mark's three non-score states, shown once near large marks. */
export function ScoreMarkKey({ className }: { className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted", className)}>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-2.5 w-4 bg-score-mark-strong" />
        Longer petal, more checks pass
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-2.5 w-4 border border-dashed border-score-mark-gap" />
        Unknown: inputs missing
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-2.5 w-4 border border-dotted border-score-mark-gap" />
        Not applicable
      </li>
    </ul>
  );
}
